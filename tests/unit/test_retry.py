"""Unit tests for pipeline.retry."""
import pytest

from pipeline.retry import (
    PERMANENT_PATTERNS,
    PermanentError,
    RetryExhaustedError,
    TransientError,
    is_permanent_text,
    with_retry,
)


def test_known_permanent_patterns_are_recognised():
    samples = [
        "[Errno 2] No reference data file found for testcity_testland",
        "unable to fetch city boundary for ciudad-del-este",
        "API returned no results found for query",
        "Sentinel-2 data not found for year 2025",
        "ghsl data download failed with status 500",
        "building footprints download failed",
        "built-up density computation failed",
        "reference data creation failed",
        "weight file not found: checkpoint/global.weights.h5",
        "model weight file not found",
        "classified raster not found in output/",
        "aoi file not found at data/raw/aoi",
    ]
    for text in samples:
        assert is_permanent_text(text), f"expected permanent signature in: {text}"


def test_permanent_patterns_table_is_non_empty():
    assert len(PERMANENT_PATTERNS) >= 12


def test_is_permanent_text_case_insensitive_and_negative():
    assert is_permanent_text("AOI File Not Found.")
    assert not is_permanent_text("all downloads succeeded")


def test_retries_transient_and_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("network hiccup")
        return "ok"

    assert with_retry(flaky, attempts=3, backoff_seconds=0) == "ok"
    assert calls["n"] == 3


def test_permanent_error_never_retried():
    calls = {"n": 0}

    def fatal():
        calls["n"] += 1
        raise PermanentError("classified raster not found")

    with pytest.raises(PermanentError):
        with_retry(fatal, attempts=5, backoff_seconds=0)
    assert calls["n"] == 1


def test_permanent_text_in_exception_stops_retrying():
    calls = {"n": 0}

    def fatal():
        calls["n"] += 1
        raise RuntimeError("no reference data file found for testcity")

    with pytest.raises(RuntimeError):
        with_retry(fatal, attempts=5, backoff_seconds=0)
    assert calls["n"] == 1


def test_retries_exhausted_raises():
    def always_fails():
        raise ConnectionError("dropped connection")

    with pytest.raises(RetryExhaustedError):
        with_retry(always_fails, attempts=2, backoff_seconds=0)