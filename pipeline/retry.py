"""Retry logic and permanent-error detection.

The ai-dua-mapping framework only reports exit codes and free-form log text,
so we can not reliably tell a transient failure (e.g. a dropped download)
from a permanent one (e.g. missing AOI) just from the exit code. The policy
used everywhere in this package:

1. If an operation raises ``PermanentError``, or a failure message matches
   one of ``PERMANENT_PATTERNS``, fail immediately (no retry).
2. Any other failure is retried up to ``attempts`` times with linear backoff.
3. If the retries are exhausted, ``RetryExhaustedError`` is raised.

The permanent patterns below were extracted verbatim from the error messages
emitted by the real framework code (prepare_data.py, pipelines.py,
sdg_stats.py, adm_boundaries.py), so they will only need updating if the
IDEAtlas repository changes its wording.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# Substrings that indicate a permanent failure inside the framework. Matching
# is case-insensitive.
PERMANENT_PATTERNS = (
    "no reference data file found",
    "unable to fetch city boundary",
    "no results found for query",
    "sentinel-2 data not found",
    "ghsl data download failed",
    "building footprints download failed",
    "built-up density computation failed",
    "reference data creation failed",
    "weight file not found",
    "model weight file not found",
    "classified raster not found",
    "aoi file not found",
)


class TransientError(Exception):
    """A failure that is safe to retry (network hiccup, timeouts, ...)."""


class PermanentError(TransientError):
    """A failure that will not succeed if retried (missing data, bad config)."""


class RetryExhaustedError(TransientError):
    """Raised when the retry budget is used up."""


def is_permanent_text(text: str) -> bool:
    """Return True if ``text`` contains any known permanent-failure signature."""
    if not text:
        return False
    lowered = text.lower()
    return any(pattern in lowered for pattern in PERMANENT_PATTERNS)


def _exception_is_permanent(exc: BaseException) -> bool:
    if isinstance(exc, PermanentError):
        return True
    if isinstance(exc, TransientError):
        return False
    return is_permanent_text(str(exc))


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
    logger: Optional[logging.Logger] = None,
) -> T:
    """Call ``fn`` with retries and backoff.

    Args:
        fn: zero-argument callable returning the success value.
        attempts: total number of attempts (first call counts as one).
        backoff_seconds: base sleep before the nth retry (1 x backoff, 2 x ...).

    Returns:
        The return value of the first successful call to ``fn``.

    Raises:
        PermanentError: if a failure is detected as permanent.
        RetryExhaustedError: if all attempts fail with transient errors.
    """
    log = logger or logging.getLogger(__name__)
    last_error: Optional[BaseException] = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - any error is a candidate
            last_error = exc
            if _exception_is_permanent(exc):
                raise

            if attempt >= attempts:
                break

            wait = backoff_seconds * attempt
            log.warning(
                "Attempt %d/%d failed with a transient error (%s); retrying in %.1f s",
                attempt,
                attempts,
                exc,
                wait,
            )
            time.sleep(wait)

    raise RetryExhaustedError(
        f"Operation failed after {attempts} attempt(s); last error: {last_error}"
    ) from last_error