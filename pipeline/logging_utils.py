"""Structured JSON logging for automation components.

Each component logs one JSON object per line, both to stderr and (when a
``log_dir`` is given) to a per-city ``.jsonl`` file. Keeping our own log lines
separate from the framework's ``main.py`` output makes it easy to search
(e.g. for fallback decisions or permanent errors) without parsing free text.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

DEFAULT_LEVEL = logging.INFO


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    def __init__(self, context: Optional[dict] = None) -> None:
        super().__init__()
        self._context = context or {}

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(self._context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(
    component: str,
    *,
    city: Optional[str] = None,
    year: Optional[int] = None,
    log_dir: Optional[str] = None,
    level: int = DEFAULT_LEVEL,
) -> logging.Logger:
    """Return a configured logger for ``component``.

    The logger is created once per (component, city, year); subsequent calls
    return the same instance so duplicate handlers are never added.
    """
    name = component if city is None else f"{city}.{component}"
    logger = logging.getLogger(f"ideatlas.{name}")
    logger.setLevel(level)
    logger.propagate = False

    if getattr(logger, "_ideatlas_configured", False):
        return logger

    context = {"component": component}
    if city:
        context["city"] = city
    if year is not None:
        context["year"] = year

    formatter = JsonFormatter(context)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        file_name = f"{city}_{year}_{component}.jsonl" if city else f"{component}.jsonl"
        file_handler = logging.FileHandler(os.path.join(log_dir, file_name), encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger._ideatlas_configured = True  # type: ignore[attr-defined]
    return logger