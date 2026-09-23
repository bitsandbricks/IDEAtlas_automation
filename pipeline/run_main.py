"""Invoke the real ai-dua-mapping ``main.py`` as a subprocess.

This is the only place where the framework is executed, and we treat it as a
semi-opaque box:

- The framework is launched with ``subprocess`` from inside the cloned
  repository (it loads ``config.yaml`` and its own modules relative to the
  current directory).
- Its stdout/stderr is captured into ``log/<city>_<year>_<task>.log``.
- Failures are retried per :mod:`pipeline.retry` policy, unless the captured
  log contains a known permanent-error signature.
- If the effective task is ``train``/``finetune``, a classification run is
  **chained automatically afterwards** using the city weights saved by the
  framework during training (so the pipeline always ends with a DUA map;
  required by the SDG-stats step). The weights are resolved *after* training,
  so the chained classify really uses the freshly trained model.
- On full success a ``.main_done`` marker is created, which the Makefile uses
  for idempotency (the whole chain is skipped on subsequent runs).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

from pipeline.config import load_config
from pipeline.logging_utils import get_logger
from pipeline.retry import PermanentError, is_permanent_text, with_retry


def load_task_mode(path: str) -> dict:
    """Read a task_mode.json produced by resolve_task_mode."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"task-mode file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        task_mode = json.load(fh)
    for key in ("city", "year", "task_effective", "task_requested"):
        if key not in task_mode:
            raise ValueError(f"task-mode file {path} is missing key '{key}'")
    return task_mode


def build_invocations(
    cfg: SimpleNamespace,
    task_effective: str,
    logger=None,
) -> List[Tuple[str, List[str]]]:
    """Build the list of ``(label, argv)`` main.py calls to run."""
    if logger is None:
        logger = logging.getLogger(__name__)

    argv_prefix = [sys.executable, "main.py"]

    def call(task: str, weights: str) -> List[str]:
        argv = argv_prefix + [
            "--task", task,
            "--city", cfg.city,
            "--country", cfg.country,
            "--year", str(cfg.year),
        ]
        if weights:
            argv += ["--weights", weights]
        return argv

    if task_effective in ("classify", "sdg_stats"):
        return [(task_effective, call(task_effective, cfg.weights))]

    # train / finetune: run the model step first. The chained classify step is
    # *not* built here, because it must use the city weights that the
    # framework saves *during* training (see ``chain_classify``).
    return [(task_effective, call(task_effective, cfg.weights))]


def chain_classify(cfg: SimpleNamespace, logger=None) -> Tuple[str, List[str]]:
    """Build the classify step that automatically follows train/finetune.

    The weights decision happens at call time (i.e. after training finished),
    so the freshly saved city model is used whenever it exists; otherwise the
    global pre-trained model is used.
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    city_weights = cfg.city_weights_path
    classify_weights = city_weights if os.path.exists(city_weights) else None
    if classify_weights:
        logger.info("Chaining 'classify' with city weights saved at %s", city_weights)
    else:
        logger.warning(
            "No city weights found at %s after training; chaining 'classify' with the global model.",
            city_weights,
        )

    argv = [sys.executable, "main.py",
            "--task", "classify",
            "--city", cfg.city,
            "--country", cfg.country,
            "--year", str(cfg.year)]
    if classify_weights:
        argv += ["--weights", classify_weights]
    return ("classify", argv)


def _run_command(cmd: List[str], cfg: SimpleNamespace, log_path: str, logger) -> int:
    """Run ``cmd`` with output captured to ``log_path`` (also echoed to console)."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True) if os.path.dirname(log_path) else None
    logger.info("Running: %s", " ".join(cmd))
    logger.info("Log file: %s", log_path)

    with open(log_path, "w", encoding="utf-8") as log_fh:
        process = subprocess.Popen(
            cmd,
            cwd=cfg.ai_dua_mapping_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        with process.stdout:
            for line in process.stdout:
                log_fh.write(line)
                sys.stdout.write(line)
                sys.stdout.flush()
        return process.wait()


def run_task_command(cfg: SimpleNamespace, cmd: List[str], log_path: str, logger) -> None:
    """Run one main.py call with the retry policy from :mod:`pipeline.retry`."""

    def attempt() -> None:
        return_code = _run_command(cmd, cfg, log_path, logger)
        if return_code == 0:
            return
        with open(log_path, encoding="utf-8") as fh:
            log_text = fh.read()
        if is_permanent_text(log_text):
            raise PermanentError(
                "main.py failed with a non-retryable (permanent) error; "
                "see the log for details."
            )
        raise RuntimeError(
            f"main.py exited with code {return_code}; log: {log_path}"
        )

    with_retry(
        attempt,
        attempts=cfg.retry_max_attempts,
        backoff_seconds=cfg.retry_backoff_seconds,
        logger=logger,
    )


def create_done_marker(cfg: SimpleNamespace) -> str:
    marker = os.path.join(cfg.state_dir, f"{cfg.city}_{cfg.year}", ".main_done")
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    Path(marker).touch()
    return marker


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the real ai-dua-mapping main.py for a city/year with retries, "
            "log capture and automatic classify chaining. Requires a "
            "task_mode.json produced by resolve_task_mode."
        )
    )
    parser.add_argument("--city", required=True, help="City name (e.g. asuncion).")
    parser.add_argument("--country", default=None, help="Country override (city config default).")
    parser.add_argument("--year", type=int, default=None, help="Year override (city config default).")
    parser.add_argument("--task-mode", required=True, help="Path to task_mode.json.")
    parser.add_argument("--weights", default=None, help="Optional weights file for classify/finetune.")
    args = parser.parse_args(argv)

    cfg = load_config(args.city, country=args.country, year=args.year, weights=args.weights)
    logger = get_logger("run_main", city=cfg.city, year=cfg.year, log_dir=cfg.log_dir)

    task_mode = load_task_mode(args.task_mode)
    effective = task_mode["task_effective"]
    logger.info(
        "Effective task for %s/%d: %s (requested: %s)",
        cfg.city,
        cfg.year,
        effective,
        task_mode["task_requested"],
    )

    invocations = build_invocations(cfg, effective, logger)
    for label, cmd in invocations:
        log_path = os.path.join(cfg.log_dir, f"{cfg.city}_{cfg.year}_{label}.log")
        run_task_command(cfg, cmd, log_path, logger)
        logger.info("Completed step '%s' for %s/%d", label, cfg.city, cfg.year)

    if effective in ("train", "finetune"):
        label, cmd = chain_classify(cfg, logger)
        log_path = os.path.join(cfg.log_dir, f"{cfg.city}_{cfg.year}_{label}.log")
        run_task_command(cfg, cmd, log_path, logger)
        logger.info("Completed chained step '%s' for %s/%d", label, cfg.city, cfg.year)

    marker = create_done_marker(cfg)
    print(f"SUCCESS: all steps finished for {cfg.city}/{cfg.year}; marker: {marker}")
    logger.info("Marker created: %s", marker)
    return 0


if __name__ == "__main__":
    sys.exit(main())