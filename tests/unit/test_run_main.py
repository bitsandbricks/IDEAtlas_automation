"""Unit tests for pipeline.run_main (invocation building, no framework run)."""
import json
import os

import pytest

from pipeline.config import load_config
from pipeline.run_main import build_invocations, chain_classify, load_task_mode


def test_build_invocations_classify_and_sdg():
    cfg = load_config("asuncion")
    for task in ("classify", "sdg_stats"):
        invocations = build_invocations(cfg, task)
        assert [label for label, _ in invocations] == [task]
        argv = invocations[0][1]
        assert "--task" in argv and cfg.city in argv and "main.py" in argv

def test_build_invocations_train_does_not_chain_early():
    cfg = load_config("asuncion")
    labels = [label for label, _ in build_invocations(cfg, "finetune")]
    assert labels == ["finetune"]


def test_chain_classify_uses_global_weights_when_city_weights_missing(tmp_path):
    cfg = load_config("testcity-ref")
    cfg.city_weights_path = str(tmp_path / "missing.h5")
    label, argv = chain_classify(cfg)
    assert label == "classify"
    assert "--weights" not in argv


def test_chain_classify_uses_city_weights_when_present(tmp_path):
    weights = tmp_path / "testcity_ref_testland.s2.bd.mbcnn.weights.h5"
    weights.write_bytes(b"\x00")
    cfg = load_config("testcity-ref")
    cfg.city_weights_path = str(weights)
    label, argv = chain_classify(cfg)
    assert label == "classify"
    assert argv[-2:] == ["--weights", str(weights)]


def test_load_task_mode_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_task_mode(str(tmp_path / "nope.json"))


def test_load_task_mode_missing_key(tmp_path):
    path = tmp_path / "tm.json"
    path.write_text(json.dumps({"city": "asuncion"}))
    with pytest.raises(ValueError):
        load_task_mode(str(path))


def test_load_task_mode_ok(tmp_path):
    path = tmp_path / "tm.json"
    path.write_text(json.dumps({
        "city": "asuncion", "year": 2025,
        "task_effective": "classify", "task_requested": "classify",
    }))
    assert load_task_mode(str(path))["task_effective"] == "classify"