"""Unit tests for the pure helpers of pipeline.sdg_stats_wrapper."""
import json

import pytest

from pipeline.config import load_config
from pipeline.sdg_stats_wrapper import (
    build_output_json,
    ghsl_pop_pattern,
    locate_classified_raster,
    summary_to_json,
    write_json_atomic,
)
from pipeline.retry import PermanentError


class StubSummary:
    def __init__(self, data):
        self._data = data

    def to_dict(self, orient="index"):
        assert orient == "index"
        return self._data


def test_summary_to_json_coerces_values():
    stub = StubSummary({
        "formal": {"area_sqkm": 1.23456789, "pop": 123456.789, "area_pct": 62.5},
        "informal": {"area_sqkm": 0.740072343, "pop": 50000.0, "area_pct": 37.5},
    })
    result = summary_to_json(stub)
    assert result["formal"]["area_sqkm"] == 1.234568
    assert result["formal"]["pop"] == 123456.789
    assert result["informal"]["area_sqkm"] == 0.740072


def test_summary_to_json_accepts_empty_summary():
    assert summary_to_json(StubSummary({})) == {}


def test_build_output_json_totals():
    cfg = load_config("asuncion")
    summary = {
        "formal": {"area_ha": 640.0, "pop": 100000.0},
        "informal": {"area_ha": 160.0, "pop": 25000.0},
    }
    task_mode = {"task_requested": "classify", "task_effective": "classify",
                 "fallback_reason": None, "reference_data_found": False}
    payload = build_output_json(cfg, task_mode, summary)

    assert payload["city"] == "asuncion"
    assert payload["year"] == 2025
    assert payload["totals"]["built_up_area_ha"] == 800.0
    assert payload["totals"]["built_up_area_km2"] == 8.0
    assert payload["totals"]["built_up_population"] == 125000.0
    assert payload["classified_raster"] == cfg.classified_raster_name
    assert payload["task_effective"] == "classify"
    assert payload["summary"] == summary
    assert "generated_at" in payload


def test_build_output_json_empty_summary():
    cfg = load_config("asuncion")
    payload = build_output_json(cfg, {}, None)
    assert payload["totals"] == {
        "built_up_area_ha": 0.0,
        "built_up_area_km2": 0.0,
        "built_up_population": 0.0,
    }


def test_locate_classified_raster_missing_raises(tmp_path):
    cfg = load_config("testcity")
    cfg.classified_raster_path = str(tmp_path / "missing.tif")
    with pytest.raises(PermanentError):
        locate_classified_raster(cfg)


def test_locate_classified_raster_found(tmp_path):
    existing = tmp_path / "found.tif"
    existing.write_bytes(b"\x00")
    cfg = load_config("testcity")
    cfg.classified_raster_path = str(existing)
    assert locate_classified_raster(cfg) == str(existing)


def test_ghsl_pop_pattern_returns_cfg_value():
    cfg = load_config("testcity")
    assert ghsl_pop_pattern(cfg) == cfg.ghsl_pop_pattern
    assert ghsl_pop_pattern(cfg).endswith("TES_GHS_POP_*.tif")


def test_write_json_atomic(tmp_path):
    target = tmp_path / "out" / "sdg_stats.json"
    write_json_atomic(str(target), {"totals": {"x": 1}})
    with open(target, encoding="utf-8") as fh:
        assert json.load(fh) == {"totals": {"x": 1}}