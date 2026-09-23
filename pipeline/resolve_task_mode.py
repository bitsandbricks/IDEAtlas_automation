"""Decide which ai-dua-mapping task should actually run.

The framework cannot fall back gracefully: if ``train`` or ``finetune`` is
requested but no reference (DUA) data exists for the city, its data
preparation step aborts with a hard exit. This module prevents that by
inspecting the same files the framework inspects *before* invoking it.

Decision rule
-------------
- ``classify`` and ``sdg_stats`` never need reference data -> keep as requested.
- ``train`` / ``finetune`` need reference data. If none is found, fall back
  to ``classify`` (use the pre-trained global model) and record why.

The chosen task is written to a ``task_mode.json`` file which ``run_main``
and ``sdg_stats_wrapper`` then consume, so every downstream step knows the
requested task, the task that actually ran, and the fallback reason.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from types import SimpleNamespace
from typing import List, Optional, Tuple

from pipeline.config import load_config, load_global
from pipeline.logging_utils import get_logger

TASKS_REQUIRING_REFERENCE = ("train", "finetune")
FALLBACK_REASON_MISSING_REFERENCE = "reference_data_not_found"


def find_reference_files(cfg: SimpleNamespace) -> List[str]:
    """List existing reference-data files for the city/year.

    Mirrors the file-name patterns that ``preprocessing/prepare_data.py``
    itself looks for at runtime (with and without a ``_vN`` version suffix,
    as both ``.tif`` and a GeoJSON sources).
    """
    norm = cfg.city_normalized
    year = cfg.year
    patterns = (
        f"{norm}_reference_{year}_v*.tif",
        f"{norm}_reference_{year}_v*.geojson",
        f"{norm}_reference_{year}.geojson",
    )
    found: List[str] = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(os.path.join(cfg.reference_dir, pattern))))
    return found


def resolve_task(
    task_requested: str,
    reference_files: List[str],
) -> Tuple[str, Optional[str]]:
    """Return ``(task_effective, fallback_reason)``.

    ``fallback_reason`` is ``None`` when the requested task is kept.
    """
    if task_requested in TASKS_REQUIRING_REFERENCE and not reference_files:
        return "classify", FALLBACK_REASON_MISSING_REFERENCE
    return task_requested, None


def build_task_mode(cfg: SimpleNamespace, task_requested: str, logger=None) -> dict:
    """Compute and return the task decision for the city."""
    reference_files = find_reference_files(cfg)
    task_effective, fallback_reason = resolve_task(task_requested, reference_files)

    if task_effective != task_requested:
        log = logger or get_logger("resolve_task_mode", city=cfg.city, year=cfg.year)
        log.warning(
            "Fallback: requested task '%s' cannot run without reference data; "
            "using '%s' instead. Expected files matching "
            "data/raw/reference_data/%s_reference_%d_v*.geojson.",
            task_requested,
            task_effective,
            cfg.city_normalized,
            cfg.year,
        )

    return {
        "city": cfg.city,
        "country": cfg.country,
        "year": cfg.year,
        "task_requested": task_requested,
        "task_effective": task_effective,
        "fallback_reason": fallback_reason,
        "reference_data_found": bool(reference_files),
        "reference_files": reference_files,
    }


def write_json_atomic(path: str, payload: dict) -> str:
    """Persist ``payload`` to ``path`` so a reader never sees a half-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)
    return path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decide which ai-dua-mapping task actually runs for a city "
            "(falling back to classify when requested train/finetune has no "
            "reference data) and write the decision to a JSON file."
        )
    )
    parser.add_argument("--city", required=True, help="City name (e.g. asuncion).")
    parser.add_argument("--country", default=None, help="Country override (city config default).")
    parser.add_argument("--year", type=int, default=None, help="Year override (city config default).")
    parser.add_argument("--task", default="classify", help="Requested task (classify/finetune/train).")
    parser.add_argument("--out", required=True, help="Path to write task_mode.json.")
    args = parser.parse_args(argv)

    cfg = load_config(args.city, country=args.country, year=args.year, task=args.task)
    logger = get_logger("resolve_task_mode", city=cfg.city, year=cfg.year, log_dir=cfg.log_dir)

    task_mode = build_task_mode(cfg, args.task, logger=logger)
    write_json_atomic(args.out, task_mode)

    print(json.dumps(task_mode, indent=2, ensure_ascii=False))
    logger.info(
        "task resolved: requested='%s' effective='%s' fallback_reason='%s'",
        task_mode["task_requested"],
        task_mode["task_effective"],
        task_mode["fallback_reason"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())