"""Consolidate every per-city ``sdg_stats.json`` into one report.

Two outputs are produced in the configured ``outputs_dir``:

- ``ideatlas_report.md``: a human-readable markdown summary with per-city
  tables and a grand totals table.
- ``ideatlas_report.xlsx``: the same content as spreadsheets (one summary
  sheet plus one sheet per city) for further analysis in QGIS/Excel.

The report is city-agnostic: it reads whatever ``outputs/<city>_<year>_sdg_stats.json``
documents exist at the time it runs.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from pipeline.config import load_global
from pipeline.logging_utils import get_logger


def collect_reports(outputs_dir: str) -> List[dict]:
    """Read all per-city SDG statistics documents found in ``outputs_dir``."""
    paths = sorted(glob.glob(os.path.join(outputs_dir, "*_sdg_stats.json")))
    reports: List[dict] = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            reports.append(json.load(fh))
    return reports


def _fmt(value, is_area: bool = False) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if is_area:
        return f"{number:,.2f}"
    return f"{number:,.0f}" if abs(number) >= 1000 else f"{number:.2f}"


def _row(report: dict) -> dict:
    totals = report.get("totals") or {}
    summary = report.get("summary") or {}
    informal = summary.get("informal") or {}
    formal = summary.get("formal") or {}
    total_built_area_km2 = totals.get("built_up_area_km2")
    dua_area_km2 = informal.get("area_sqkm")
    dua_area_pct = informal.get("area_pct")
    dua_pop = informal.get("pop")
    dua_pop_pct = informal.get("pop_pct")
    total_pop = totals.get("built_up_population")
    ndua_pop_pct = formal.get("pop_pct")

    shared = dict(
        city=report.get("city") or "?",
        country=report.get("country") or "?",
        year=report.get("year"),
        model=report.get("model") or "?",
        task_requested=report.get("task_requested"),
        task_effective=report.get("task_effective"),
        fallback_reason=report.get("fallback_reason"),
        reference_data_found=report.get("reference_data_found"),
        generated_at=report.get("generated_at"),
        built_up_area_km2=total_built_area_km2,
        ndua_area_km2=formal.get("area_sqkm"),
        dua_area_km2=dua_area_km2,
        dua_area_pct=dua_area_pct,
        built_up_population=total_pop,
        ndua_pop=formal.get("pop"),
        dua_pop=dua_pop,
        dua_pop_pct=dua_pop_pct,
        ndua_pop_pct=ndua_pop_pct,
        formal=formal,
        informal=informal,
    )
    return dict(shared)


def build_markdown(reports: List[dict], generated_at: str) -> str:
    rows = [_row(r) for r in reports]
    lines: List[str] = []
    lines.append("# IDEAtlas SDG 11.1.1 report")
    lines.append("")
    lines.append(f"Generated: {generated_at}")
    lines.append("")
    lines.append("Proportion of the urban population living in deprived urban areas")
    lines.append("(DUA), computed with the IDEAtlas mapping pipeline.")
    lines.append("")

    # Grand summary table.
    lines.append("## Summary")
    lines.append("")
    header = (
        "| City | Year | Effective task | Fallback | Built-up (km2) | "
        "DUA area (km2) | DUA area (%) | Built-up population | DUA population | SDG 11.1.1 (%) |"
    )
    lines.append(header)
    lines.append("|" + "---|" * header.count("|"))
    for r in rows:
        lines.append(
            "| {city} | {year} | {task_effective} | {fallback} | {area_km2} | "
            "{dua_km2} | {dua_pct} | {pop} | {dua_pop} | {sdg} |".format(
                city=r["city"],
                year=r["year"],
                task_effective=r["task_effective"] or "n/a",
                fallback=r["fallback_reason"] or "none",
                area_km2=_fmt(r["built_up_area_km2"], is_area=True),
                dua_km2=_fmt(r["dua_area_km2"], is_area=True),
                dua_pct=_fmt(r["dua_area_pct"]),
                pop=_fmt(r["built_up_population"]),
                dua_pop=_fmt(r["dua_pop"]),
                sdg=_fmt(r["dua_pop_pct"]),
            )
        )
    lines.append("")

    # Per-city detail.
    for r in rows:
        lines.append(f"## {r['city']} ({r['year']})")
        lines.append("")
        lines.append(
            f"- Effective task: {r['task_effective'] or 'n/a'} "
            f"(requested: {r['task_requested'] or 'n/a'}, "
            f"fallback reason: {r['fallback_reason'] or 'none'})"
        )
        lines.append(f"- Model: {r['model']}")
        lines.append(f"- Generated at: {r['generated_at'] or 'n/a'}")
        lines.append("")
        lines.append("| Indicator | Formal (NDUA) | Deprived (DUA) |")
        lines.append("|---|---:|---:|")
        for metric, label in (
            ("area_sqkm", "Built-up area (km2)"),
            ("area_pct", "Built-up area (%)"),
            ("pop", "Population"),
            ("pop_pct", "Population (%)"),
        ):
            formal_val = r["formal"].get(metric)
            informal_val = r["informal"].get(metric)
            is_area = metric == "area_sqkm"
            lines.append(f"| {label} | {_fmt(formal_val, is_area=is_area)} | {_fmt(informal_val, is_area=is_area)} |")
        lines.append("")

    lines.append("---")
    lines.append("Note: population figures are derived from the GHSL global population")
    lines.append("grid, which tends to underestimate populations in deprived areas.")
    return "\n".join(lines)


def build_xlsx(reports: List[dict], out_path: str) -> None:
    """Write the report as an Excel workbook (summary + one sheet per city)."""
    from openpyxl import Workbook  # imported locally to keep the md path light

    wb = Workbook()
    wb.remove(wb.active)

    rows = [_row(r) for r in reports]
    summary_ws = wb.create_sheet("Summary")
    headers = [
        "City", "Country", "Year", "Model", "Task (requested)", "Task (effective)",
        "Fallback reason", "Built-up (km2)", "DUA area (km2)", "DUA area (%)",
        "Built-up population", "DUA population", "SDG 11.1.1 (%)",
    ]
    summary_ws.append(headers)
    for r in rows:
        summary_ws.append([
            r["city"], r["country"], r["year"], r["model"],
            r["task_requested"], r["task_effective"], r["fallback_reason"],
            r["built_up_area_km2"], r["dua_area_km2"], r["dua_area_pct"],
            r["built_up_population"], r["dua_pop"], r["dua_pop_pct"],
        ])

    for r in rows:
        ws = wb.create_sheet(f"{r['city']}_{r['year']}")
        ws.append(["City", r["city"]])
        ws.append(["Country", r["country"]])
        ws.append(["Year", r["year"]])
        ws.append(["Task (requested)", r["task_requested"]])
        ws.append(["Task (effective)", r["task_effective"]])
        ws.append(["Fallback reason", r["fallback_reason"]])
        ws.append(["Model", r["model"]])
        ws.append([])
        ws.append(["Indicator", "Formal (NDUA)", "Deprived (DUA)"])
        for metric, label in (
            ("area_sqkm", "Built-up area (km2)"),
            ("area_ha", "Built-up area (ha)"),
            ("area_pct", "Built-up area (%)"),
            ("pop", "Population"),
            ("pop_pct", "Population (%)"),
        ):
            ws.append([
                label,
                r["formal"].get(metric),
                r["informal"].get(metric),
            ])
        ws.append([])
        ws.append(["Generated at", r["generated_at"]])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate per-city sdg_stats.json documents into one report."
    )
    parser.add_argument("--outputs-dir", default=None, help="Directory with sdg_stats.json files.")
    parser.add_argument("--out-md", default=None, help="Destination for the markdown report.")
    parser.add_argument("--out-xlsx", default=None, help="Destination for the xlsx report.")
    args = parser.parse_args(argv)

    cfg = load_global()
    outputs_dir = args.outputs_dir or cfg.outputs_dir
    logger = get_logger("generate_report", log_dir=cfg.log_dir)

    reports = collect_reports(outputs_dir)
    if not reports:
        logger.error(
            "No sdg_stats.json documents found in %s. Run 'make all-cities' first.",
            outputs_dir,
        )
        print(f"No SDG statistics documents found in {outputs_dir}.", file=sys.stderr)
        return 1

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_md = args.out_md or os.path.join(outputs_dir, "ideatlas_report.md")
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(build_markdown(reports, generated_at))
    print(f"Markdown report written to {out_md}")

    out_xlsx = args.out_xlsx or os.path.join(outputs_dir, "ideatlas_report.xlsx")
    try:
        build_xlsx(reports, out_xlsx)
    except ImportError:
        logger.warning("openpyxl is not installed; skipping the xlsx report.")
        print("Skipped xlsx report (openpyxl not installed).", file=sys.stderr)
    else:
        print(f"Excel report written to {out_xlsx}")

    logger.info("Report generated: %d city document(s).", len(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())