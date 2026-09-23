"""Compute SDG 11.1.1 statistics for a city and persist a JSON summary.

The framework's ``main.py --task sdg_stats`` only prints its statistics to the
console and optionally writes a GeoPackage/GeoJSON of polygons; it does not
produce a machine-readable summary. The console output is easy to mis-parse,
so this wrapper imports the framework's own ``utils.sdg_stats`` module and
calls ``compute_sdg111_stats`` **as a function**, capturing the ``summary``
DataFrame it already returns, and persists it together with the task-mode
metadata into ``outputs/<city>_<year>_sdg_stats.json``.

Steps (mirroring the framework's own ``sdg_stats.main()``):

1. Locate the classified raster (written by ``classify``).
2. Locate the cached GHSL population raster, downloading it if needed.
3. Polygonize the formal/informal built-up classes (labels 2/3).
4. Call ``compute_sdg111_stats`` and serialize its ``summary``.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional, Tuple

from pipeline.config import load_config
from pipeline.logging_utils import get_logger
from pipeline.retry import PermanentError, TransientError, with_retry

FORMAL_CLASS = 2
INFORMAL_CLASS = 3

# Redemption for imports of the framework module (kept local to the run step).
_REPO_IMPORTED: Optional[SimpleNamespace] = None


def _cast(value) -> float:
    """Coerce numpy/pandas scalars to plain JSON-safe numbers."""
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return value


def summary_to_json(summary_df) -> dict:
    """Turn the framework's summary DataFrame into ``{class: {metric: value}}``.

    ``summary_df`` only needs a ``to_dict(orient="index")`` behaviour; the
    wrapped ``summary`` produced by ``compute_sdg111_stats`` already has rows
    indexed ``formal`` / ``informal`` with columns ``area_m2, pop, area_ha,
    area_sqkm, area_pct, pop_pct``.
    """
    data = summary_df.to_dict(orient="index")
    return {
        str(cls): {str(metric): _cast(value) for metric, value in metrics.items()}
        for cls, metrics in data.items()
    }


def build_output_json(cfg: SimpleNamespace, task_mode: dict, summary_json: dict) -> dict:
    """Assemble the final persisted JSON document."""
    rows = summary_json or {}
    total_area_ha = sum(rows.get(cls, {}).get("area_ha", 0.0) for cls in rows)
    total_population = sum(rows.get(cls, {}).get("pop", 0.0) for cls in rows)

    return {
        "city": cfg.city,
        "country": cfg.country,
        "year": cfg.year,
        "model": cfg.model,
        "task_requested": task_mode.get("task_requested"),
        "task_effective": task_mode.get("task_effective"),
        "fallback_reason": task_mode.get("fallback_reason"),
        "reference_data_found": task_mode.get("reference_data_found"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "classified_raster": cfg.classified_raster_name,
        "totals": {
            "built_up_area_ha": _cast(total_area_ha),
            "built_up_area_km2": _cast(total_area_ha / 100.0),
            "built_up_population": _cast(total_population),
        },
        "summary": summary_json,
    }


def locate_classified_raster(cfg: SimpleNamespace) -> str:
    """Return the classified raster path or raise a clear permanent error."""
    path = cfg.classified_raster_path
    if not os.path.exists(path):
        raise PermanentError(
            f"Classified raster not found at {path}. Run the classification "
            f"step first (make city CITY={cfg.city} TASK=classify) and then "
            f"re-run the SDG statistics step."
        )
    return path


def ghsl_pop_pattern(cfg: SimpleNamespace) -> str:
    return cfg.ghsl_pop_pattern


def ensure_ghsl_pop(cfg: SimpleNamespace, sdg) -> str:
    """Return a cached GHSL population raster, downloading it if needed.

    ``sdg`` is the imported ``utils.sdg_stats`` module, which provides both
    the ``fetch_ghsl`` downloader and the stdout/suppression helper.
    """
    existing = glob.glob(ghsl_pop_pattern(cfg))
    if existing:
        return existing[0]

    if not os.path.exists(cfg.aoi_path):
        raise PermanentError(f"AOI file not found: {cfg.aoi_path}")

    logger = get_logger("sdg_stats_wrapper", city=cfg.city, year=cfg.year, log_dir=cfg.log_dir)
    logger.info("GHSL population raster not cached; downloading from GHSL.")
    downloader = sdg.fetch_ghsl.GHSLDownloader(
        temp_dir=os.path.join(cfg.ai_dua_mapping_dir, "data", "raw", "ghsl", "temp")
    )
    try:
        sdg.suppress_output(
            downloader.download_tiles,
            aoi_geojson=cfg.aoi_path,
            output_dir=os.path.join(cfg.ai_dua_mapping_dir, "data", "raw", "ghsl"),
            data_type="pop",
        )
    except (socket.timeout, ConnectionError, OSError) as exc:
        raise TransientError(f"GHSL population download failed (transient): {exc}") from exc
    except Exception as exc:
        raise TransientError(f"GHSL population download failed: {exc}") from exc

    downloaded = glob.glob(ghsl_pop_pattern(cfg))
    if not downloaded:
        raise TransientError("GHSL population download produced no usable raster files.")
    logger.info("GHSL population raster ready: %s", downloaded[0])
    return downloaded[0]


def import_framework(cfg: SimpleNamespace):
    """Import ``utils.sdg_stats`` from the cloned framework.

    Because the framework uses ``load_configs('config.yaml')`` and relative
    imports at module import time, we temporarily change the current directory
    and prepend the framework root to ``sys.path``.
    """
    global _REPO_IMPORTED
    if _REPO_IMPORTED is not None:
        return _REPO_IMPORTED

    os.chdir(cfg.ai_dua_mapping_dir)
    sys.path.insert(0, cfg.ai_dua_mapping_dir)
    import utils.sdg_stats as sdg  # noqa: PLC0415 - deliberate late import

    _REPO_IMPORTED = sdg
    return sdg


def write_json_atomic(path: str, payload: dict) -> str:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    return path


def compute_summary(cfg: SimpleNamespace, sdg, raster: str, pop_raster: str):
    """Polygonize the classified classes and return the framework summary DF."""
    gdf = sdg.polygonize_builtup_classes(raster, (FORMAL_CLASS, INFORMAL_CLASS))
    return sdg.compute_sdg111_stats(
        gdf,
        pop_raster,
        FORMAL_CLASS,
        INFORMAL_CLASS,
        cfg.city,
        cfg.country,
        cfg.year,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compute SDG 11.1.1 statistics from the classified map and the "
            "GHSL population model, and write a JSON summary with task-mode "
            "metadata."
        )
    )
    parser.add_argument("--city", required=True, help="City name (e.g. asuncion).")
    parser.add_argument("--country", default=None, help="Country override (city config default).")
    parser.add_argument("--year", type=int, default=None, help="Year override (city config default).")
    parser.add_argument("--task-mode", default=None, help="Path to task_mode.json (optional).")
    parser.add_argument("--out", required=True, help="Path to write the sdg_stats.json document.")
    args = parser.parse_args(argv)

    cfg = load_config(args.city, country=args.country, year=args.year)
    logger = get_logger("sdg_stats_wrapper", city=cfg.city, year=cfg.year, log_dir=cfg.log_dir)

    task_mode: dict = {}
    if args.task_mode and os.path.exists(args.task_mode):
        with open(args.task_mode, encoding="utf-8") as fh:
            task_mode = json.load(fh)

    raster = locate_classified_raster(cfg)
    logger.info("Classified raster: %s", raster)

    sdg = import_framework(cfg)
    pop_raster = with_retry(
        lambda: ensure_ghsl_pop(cfg, sdg),
        attempts=cfg.retry_max_attempts,
        backoff_seconds=cfg.retry_backoff_seconds,
        logger=logger,
    )
    logger.info("Population raster: %s", pop_raster)

    summary_df = compute_summary(cfg, sdg, raster, pop_raster)
    summary_json = summary_to_json(summary_df)
    payload = build_output_json(cfg, task_mode, summary_json)
    write_json_atomic(args.out, payload)

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Wrote SDG statistics JSON to %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())