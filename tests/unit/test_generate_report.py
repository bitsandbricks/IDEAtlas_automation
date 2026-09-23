"""Unit tests for pipeline.generate_report helpers."""
import json
import os

from pipeline.generate_report import build_markdown, collect_reports, main


def _report(city="asuncion", **overrides):
    payload = {
        "city": city,
        "country": "paraguay",
        "year": 2025,
        "model": "mbcnn",
        "task_requested": "classify",
        "task_effective": "classify",
        "fallback_reason": None,
        "reference_data_found": False,
        "generated_at": "2026-09-23T00:00:00+00:00",
        "totals": {
            "built_up_area_ha": 800.0,
            "built_up_area_km2": 8.0,
            "built_up_population": 125000,
        },
        "summary": {
            "formal": {"area_sqkm": 6.4, "area_ha": 640.0, "area_pct": 80.0,
                       "pop": 100000, "pop_pct": 80.0},
            "informal": {"area_sqkm": 1.6, "area_ha": 160.0, "area_pct": 20.0,
                         "pop": 25000, "pop_pct": 20.0},
        },
    }
    payload.update(overrides)
    return payload


def test_collect_reports_reads_all_documents(tmp_path):
    (tmp_path / "asuncion_2025_sdg_stats.json").write_text(json.dumps(_report()))
    (tmp_path / "encarnacion_2025_sdg_stats.json").write_text(json.dumps(_report(city="encarnacion")))
    (tmp_path / "unrelated.txt").write_text("ignored")

    reports = collect_reports(str(tmp_path))
    assert len(reports) == 2
    assert {r["city"] for r in reports} == {"asuncion", "encarnacion"}


def test_build_markdown_contains_summary_and_details():
    markdown = build_markdown(
        [_report(), _report(city="encarnacion", task_effective="finetune",
                            fallback_reason="reference_data_not_found")],
        generated_at="2026-09-23T00:00:00+00:00",
    )
    assert "# IDEAtlas SDG 11.1.1 report" in markdown
    assert "## Summary" in markdown
    assert "## asuncion (2025)" in markdown
    assert "## encarnacion (2025)" in markdown

    # Per-city sections must keep their own data (regression check): the
    # fallback reason of one city must not leak into another city's section.
    asuncion_section = markdown.split("## asuncion (2025)")[1].split("## encarnacion (2025)")[0]
    encarnacion_section = markdown.split("## encarnacion (2025)")[1]
    assert "reference_data_not_found" not in asuncion_section
    assert "fallback reason: reference_data_not_found" in encarnacion_section


def test_main_returns_1_when_no_reports(tmp_path, capsys):
    rc = main(["--outputs-dir", str(tmp_path / "empty")])
    assert rc == 1
    assert "No SDG statistics documents found" in capsys.readouterr().err


def test_main_writes_markdown(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "asuncion_2025_sdg_stats.json").write_text(json.dumps(_report()))
    out_md = tmp_path / "report.md"
    rc = main(["--outputs-dir", str(outputs), "--out-md", str(out_md)])
    assert rc == 0
    assert os.path.exists(out_md)
    content = out_md.read_text()
    assert "asuncion" in content and "SDG 11.1.1" in content