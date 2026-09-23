"""Configuration loading for the automation layer.

Two kinds of configuration exist and they are intentionally kept separate:

1. *Framework configuration* (``ai-dua-mapping/config.yaml``): model
   hyper-parameters, framework paths. Owned by the IDEAtlas repository and
   never modified here.

2. *Automation configuration* (this package): what city/country/year to
   process, where the framework was cloned, where logs/outputs/state go, and
   the retry policy. Defined in ``config/base.yaml`` plus
   ``config/cities/<city>.yaml`` and merged here.

``load_config`` also derives absolute paths (AOI file, reference-data folder,
classified raster, GHSL population pattern, saved city weights) using the
exact naming convention the framework expects, so the rest of the pipeline
never has to re-derive them.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
BASE_CONFIG_PATH = CONFIG_DIR / "base.yaml"
CITIES_CONFIG_DIR = CONFIG_DIR / "cities"

_VALID_TASKS = ("classify", "finetune", "train", "sdg_stats")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge two dicts; nested dicts are merged, scalars replaced."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize(name: str) -> str:
    """Framework file-name convention: lowercase, spaces and hyphens to underscores."""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def _make_abs_paths(cfg: dict) -> None:
    """Turn relative config dirs into absolute paths and derive framework paths."""
    repo_root = str(REPO_ROOT)
    adu_dir = str((REPO_ROOT / cfg["ai_dua_mapping_dir"]).resolve())

    cfg["ai_dua_mapping_dir"] = adu_dir
    cfg["log_dir"] = str((REPO_ROOT / cfg["log_dir"]).resolve())
    cfg["outputs_dir"] = str((REPO_ROOT / cfg["outputs_dir"]).resolve())
    cfg["state_dir"] = str((REPO_ROOT / cfg["state_dir"]).resolve())

    norm = cfg["city_normalized"]
    data_raw = Path(adu_dir) / "data" / "raw"
    cfg["aoi_path"] = str(data_raw / "aoi" / f"{norm}_aoi.geojson")
    cfg["reference_dir"] = str(data_raw / "reference_data")
    cfg["prediction_dir"] = str(Path(adu_dir) / "output")
    cfg["processed_data_dir_framework"] = str(Path(adu_dir) / "data" / "processed" / norm)

    model = cfg["model"]
    cfg["classified_raster_name"] = f"{norm}.s2.bd.{model}.{cfg['year']}.tif"
    cfg["classified_raster_path"] = str(Path(cfg["prediction_dir"]) / cfg["classified_raster_name"])
    cfg["ghsl_pop_pattern"] = str(data_raw / "ghsl" / "pop" / f"{norm[:3].upper()}_GHS_POP_*.tif")
    cfg["ghsl_built_pattern"] = str(data_raw / "ghsl" / "built" / f"{norm[:3].upper()}_*.tif")
    cfg["city_weights_path"] = str(
        Path(adu_dir) / "checkpoint" / f"{norm}.s2.bd.{model}.weights.h5"
    )
    cfg["global_weights_path"] = str(Path(adu_dir) / "checkpoint" / f"global.s2.bd.{model}.weights.h5")

    cfg["process_data_root_abs"] = repo_root
    cfg["repo_root"] = repo_root


def _set_retry_defaults(cfg: dict) -> None:
    retry = cfg.get("retry") or {}
    cfg["retry_max_attempts"] = int(retry.get("max_attempts", 3))
    cfg["retry_backoff_seconds"] = float(retry.get("backoff_seconds", 30.0))


def load_config(
    city: str,
    country: Optional[str] = None,
    year: Optional[int] = None,
    task: Optional[str] = None,
    weights: Optional[str] = None,
) -> SimpleNamespace:
    """Load the merged configuration for a given city.

    CLI values (country/year/task/weights) take precedence over the YAML
    configuration. Returns a ``SimpleNamespace``; all fields are also
    available as attributes.

    Raises:
        FileNotFoundError: if ``config/cities/<city>.yaml`` does not exist.
    """
    if not city:
        raise ValueError("A city name is required (e.g. 'asuncion').")
    city = city.strip().lower()
    city_config_path = CITIES_CONFIG_DIR / f"{city}.yaml"
    if not city_config_path.exists():
        raise FileNotFoundError(
            f"No city configuration found at {city_config_path}. "
            f"Please add config/cities/{city}.yaml."
        )

    with open(BASE_CONFIG_PATH, encoding="utf-8") as fh:
        base = yaml.safe_load(fh) or {}
    with open(city_config_path, encoding="utf-8") as fh:
        city_cfg = yaml.safe_load(fh) or {}

    cfg = _deep_merge(base, city_cfg)

    # Defaults first, then overrides so CLI values always win.
    cfg.setdefault("country", "")
    cfg.setdefault("year", 2025)
    cfg.setdefault("task", "classify")
    cfg.setdefault("weights", None)
    cfg.setdefault("model", "mbcnn")

    cfg["city"] = city
    if country:
        cfg["country"] = country
    if year is not None:
        cfg["year"] = int(year)
    if task:
        if task not in _VALID_TASKS:
            raise ValueError(f"Invalid task {task!r}; choose from {sorted(_VALID_TASKS)}.")
        cfg["task"] = task
    if weights:
        cfg["weights"] = weights

    cfg["city_normalized"] = _normalize(f"{cfg['city']}_{cfg['country']}")
    cfg["country_normalized"] = _normalize(cfg["country"])
    _set_retry_defaults(cfg)
    _make_abs_paths(cfg)
    return SimpleNamespace(**cfg)


def load_global() -> SimpleNamespace:
    """Load only the base configuration (for city-agnostic tools)."""
    with open(BASE_CONFIG_PATH, encoding="utf-8") as fh:
        base = yaml.safe_load(fh) or {}

    base.setdefault("model", "mbcnn")
    base.setdefault("country", "")
    base.setdefault("year", 2025)
    base.setdefault("task", "classify")
    base.setdefault("weights", None)
    base["city_normalized"] = ""
    _set_retry_defaults(base)
    _make_abs_paths(base)
    return SimpleNamespace(**base)