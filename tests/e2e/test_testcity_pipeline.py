"""End-to-end test: run the full pipeline on the synthetic test cities.

Requirements (see test_fixtures/README.md):
- ``ai-dua-mapping/`` cloned and the ``ideatlas`` conda environment active
  (TensorFlow, geopandas, ...), because this test imports and runs the real
  framework.
- The fixtures were generated once with ``make fixtures``.

Flow (matching the Makefile recipe for a city):
1. resolve the effective task (classify) into a task_mode.json,
2. run ``pipeline.run_main`` (which calls the framework's main.py),
3. run ``pipeline.sdg_stats_wrapper`` to compute SDG 11.1.1 statistics,
4. compare the produced statistics against the frozen expectations in
   ``test_fixtures/expected_sdg_stats_testcity.json``.

An optional ``@slow`` test fine-tunes the model on the ``testcity-ref``
fixtures (needs a few minutes / a proper GPU) and only runs when the
reference fixtures exist.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK_DIR = REPO_ROOT / "ai-dua-mapping"
EXPECTED_STATS = REPO_ROOT / "test_fixtures" / "expected_sdg_stats_testcity.json"
SDG_STATS_OUT = REPO_ROOT / "outputs" / "testcity_2025_sdg_stats.json"

pytestmark = pytest.mark.skipif(
    not (FRAMEWORK_DIR / "main.py").exists(),
    reason="ai-dua-mapping framework is not cloned",
)


def _run_module(module, *args):
    subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )


def _approx_diff(actual, expected, rtol):
    diffs = {}
    for key in expected:
        if key not in actual:
            diffs[key] = f"missing (expected {key})"
            continue
        try:
            a, e = float(actual[key]), float(expected[key])
        except (TypeError, ValueError):
            continue
        if a == 0.0 and e == 0.0:
            continue
        if abs(a - e) / max(abs(e), 1e-9) > rtol:
            diffs[key] = f"{a} vs {e}"
    return diffs


def _assert_close(actual, expected, rtol=0.05):
    for class_name in ("formal", "informal"):
        diffs = _approx_diff(actual["summary"][class_name], expected["summary"][class_name], rtol)
        assert not diffs, f"{class_name} differs (actual vs expected): {diffs}"
    totals = _approx_diff(actual["totals"], expected["totals"], rtol)
    assert not totals, f"totals differ (actual vs expected): {totals}"


def test_full_pipeline_testcity(tmp_path):
    if not EXPECTED_STATS.exists():
        pytest.fail(
            "Frozen expectations missing. Run 'make fixtures' and the pipeline once, then:\n"
            "  cp outputs/testcity_2025_sdg_stats.json "
            "test_fixtures/expected_sdg_stats_testcity.json"
        )

    task_mode = tmp_path / "task_mode.json"
    _run_module("pipeline.resolve_task_mode",
                "--city", "testcity", "--year", "2025", "--task", "classify",
                "--out", str(task_mode))

    _run_module("pipeline.run_main",
                "--city", "testcity", "--year", "2025", "--task-mode", str(task_mode))

    _run_module("pipeline.sdg_stats_wrapper",
                "--city", "testcity", "--year", "2025",
                "--task-mode", str(task_mode), "--out", str(SDG_STATS_OUT))

    with open(EXPECTED_STATS, encoding="utf-8") as fh:
        expected = json.load(fh)
    with open(SDG_STATS_OUT, encoding="utf-8") as fh:
        actual = json.load(fh)

    assert actual["task_requested"] == "classify"
    assert actual["task_effective"] == "classify"
    assert actual["fallback_reason"] is None
    _assert_close(actual, expected)


@pytest.mark.slow
def test_finetune_chain_testcity_ref(tmp_path):
    reference = (
        FRAMEWORK_DIR
        / "data" / "raw" / "reference_data"
        / "testcity_ref_testland_reference_2025_v1.geojson"
    )
    if not reference.exists():
        pytest.skip("testcity-ref reference fixtures not generated (run 'make fixtures')")

    task_mode = tmp_path / "task_mode_ref.json"
    _run_module("pipeline.resolve_task_mode",
                "--city", "testcity-ref", "--year", "2025", "--task", "finetune",
                "--out", str(task_mode))

    _run_module("pipeline.run_main",
                "--city", "testcity-ref", "--year", "2025", "--task-mode", str(task_mode))

    weights = FRAMEWORK_DIR / "checkpoint" / "testcity_ref_testland.s2.bd.mbcnn.weights.h5"
    assert weights.exists(), f"finetune did not produce city weights: {weights}"

    # Chained classify must have used the freshly trained weights.
    classified = FRAMEWORK_DIR / "output" / "testcity_ref_testland.s2.bd.mbcnn.2025.tif"
    assert classified.exists(), "chained classify did not produce a classified raster"