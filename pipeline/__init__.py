"""Automation layer for the IDEAtlas ai-dua-mapping framework.

This package contains the small set of components that orchestrate the real
IDEAtlas framework (which stays untouched in the `ai-dua-mapping/` directory):

- config:            load city/global orchestration settings
- logging_utils:     structured JSON logging for our components
- retry:             retry + permanent-error detection helpers
- resolve_task_mode: decide which framework task actually runs
- run_main:          invoke the framework's main.py as a subprocess
- sdg_stats_wrapper: compute SDG 11.1.1 stats and persist a JSON summary
- generate_report:   consolidate per-city stats into a markdown/xlsx report
"""