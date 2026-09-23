"""Unit tests for pipeline.resolve_task_mode."""
import json

import pytest

from pipeline.config import load_config
from pipeline.resolve_task_mode import (
    FALLBACK_REASON_MISSING_REFERENCE,
    build_task_mode,
    find_reference_files,
    resolve_task,
    write_json_atomic,
)


def _config_with_reference_dir(tmp_path):
    cfg = load_config("testcity-ref")
    cfg.reference_dir = str(tmp_path)
    return cfg


def test_find_reference_files_finds_matching_files(tmp_path):
    cfg = _config_with_reference_dir(tmp_path)
    (tmp_path / "testcity_ref_testland_reference_2025.geojson").write_text("{}")
    (tmp_path / "testcity_ref_testland_reference_2025_v2.geojson").write_text("{}")
    (tmp_path / "testcity_ref_testland_reference_2025_v1.tif").write_bytes(b"\x00")
    # Unrelated files must be ignored.
    (tmp_path / "asuncion_reference_2025.geojson").write_text("{}")
    (tmp_path / "something_else.txt").write_text("x")

    found = find_reference_files(cfg)
    assert len(found) == 3
    assert all("testcity_ref_testland" in f for f in found)


def test_find_reference_files_returns_empty(tmp_path):
    cfg = _config_with_reference_dir(tmp_path)
    assert find_reference_files(cfg) == []


def test_resolve_task_pure():
    assert resolve_task("classify", []) == ("classify", None)
    assert resolve_task("sdg_stats", []) == ("sdg_stats", None)
    assert resolve_task("finetune", ["x.geojson"]) == ("finetune", None)
    assert resolve_task("train", ["x.geojson"]) == ("train", None)
    effective, reason = resolve_task("finetune", [])
    assert effective == "classify"
    assert reason == FALLBACK_REASON_MISSING_REFERENCE


def test_build_task_mode_falls_back_when_no_reference(tmp_path):
    cfg = _config_with_reference_dir(tmp_path)
    task_mode = build_task_mode(cfg, "finetune")
    assert task_mode["task_requested"] == "finetune"
    assert task_mode["task_effective"] == "classify"
    assert task_mode["fallback_reason"] == FALLBACK_REASON_MISSING_REFERENCE
    assert task_mode["reference_data_found"] is False
    assert task_mode["city"] == "testcity-ref"
    assert task_mode["year"] == 2025


def test_build_task_mode_keeps_task_when_reference_exists(tmp_path):
    cfg = _config_with_reference_dir(tmp_path)
    (tmp_path / "testcity_ref_testland_reference_2025.geojson").write_text("{}")
    task_mode = build_task_mode(cfg, "finetune")
    assert task_mode["task_effective"] == "finetune"
    assert task_mode["fallback_reason"] is None
    assert task_mode["reference_data_found"] is True
    assert len(task_mode["reference_files"]) == 1


def test_write_json_atomic(tmp_path):
    target = tmp_path / "nested" / "task_mode.json"
    path = write_json_atomic(str(target), {"a": 1})
    assert path == str(target)
    with open(target, encoding="utf-8") as fh:
        assert json.load(fh) == {"a": 1}


def test_main_writes_decision(tmp_path):
    from pipeline.resolve_task_mode import main

    out = tmp_path / "task_mode.json"
    rc = main(["--city", "testcity-ref", "--task", "finetune", "--out", str(out)])
    assert rc == 0
    with open(out, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["task_requested"] == "finetune"
    assert payload["task_effective"] == "classify"


def test_main_requires_out_and_city():
    from pipeline.resolve_task_mode import main

    with pytest.raises(SystemExit):
        main([])