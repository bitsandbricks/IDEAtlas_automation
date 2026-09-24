"""Unit tests for tools.fetch_city_aois (no network; uses a fake Session)."""
import os

import pytest

from pipeline.config import load_config
from tools.fetch_city_aois import (
    FUA_MAX_AREA_KM2,
    MIN_AREA_KM2,
    _area_km2,
    _bbox_geometry,
    _first_fua_match,
    _norm_name,
    _plausible,
    fetch_aoi_geometry,
    main,
    normalize,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Canned Nominatim search responses, consumed one per request."""

    def __init__(self, responses):
        assert responses, "need at least one canned response"
        self._responses = list(responses)

    def get(self, url, params=None, headers=None, timeout=None):
        return _FakeResponse(self._responses.pop(0))


def _rect_polygon(west, south, east, north):
    return {
        "type": "Polygon",
        "coordinates": [[
            [west, south],
            [east, south],
            [east, north],
            [west, north],
            [west, south],
        ]],
    }


def test_selects_first_plausible_polygon(capsys):
    session = _FakeSession([[
        {
            "display_name": "Encarnacion, Itapua, Paraguay",
            "geojson": _rect_polygon(-55.90, -27.35, -55.83, -27.30),
        },
    ]])
    expected = _rect_polygon(-55.90, -27.35, -55.83, -27.30)
    geometry = fetch_aoi_geometry(
        session, ["Encarnacion, Itapua, Paraguay"], sleep=0.0
    )
    assert geometry == expected
    assert "selected: Encarnacion" in capsys.readouterr().out


def test_implausible_polygon_is_rejected(capsys):
    session = _FakeSession([[
        {
            "display_name": "sliver",
            "geojson": _rect_polygon(-57.640, -25.280, -57.639, -25.279),
        },
    ]])
    geometry = fetch_aoi_geometry(session, ["Somewhere"], sleep=0.0)
    out = capsys.readouterr().out
    assert geometry is None
    assert "rejected (implausible area)" in out
    assert "no polygon result from Nominatim" in out


def test_bbox_fallback_for_point_results(capsys):
    session = _FakeSession([[
        {
            "display_name": "Asuncion, Paraguay",
            "geojson": {"type": "Point", "coordinates": [-57.6343814, -25.2800459]},
            "boundingbox": ["-25.44", "-25.12", "-57.79", "-57.47"],
        },
    ]])
    geometry = fetch_aoi_geometry(session, ["Asuncion, Paraguay"], sleep=0.0)
    out = capsys.readouterr().out
    assert geometry["type"] == "Polygon"
    xs = [c[0] for c in geometry["coordinates"][0]]
    ys = [c[1] for c in geometry["coordinates"][0]]
    assert min(xs) == -57.79 and max(xs) == -57.47
    assert min(ys) == -25.44 and max(ys) == -25.12
    assert _area_km2(geometry) >= MIN_AREA_KM2
    assert "rectangular fallback" in out
    assert "verify this AOI in a GIS viewer" in out


def test_no_results_returns_none(capsys):
    session = _FakeSession([[]])
    geometry = fetch_aoi_geometry(session, ["Somewhere, Nowhere"], sleep=0.0)
    out = capsys.readouterr().out
    assert geometry is None
    assert "no polygon result from Nominatim" in out


def test_bbox_geometry_rejects_malformed():
    assert _bbox_geometry({}) is None
    assert _bbox_geometry({"boundingbox": ["1", "2", "3"]}) is None
    assert _bbox_geometry({"boundingbox": ["1", "0", "2", "3"]}) is None  # south > north
    assert _bbox_geometry({"boundingbox": ["-1", "1", "-2", "2"]}) == _rect_polygon(-2, -1, 2, 1)


@pytest.mark.parametrize("city", ["asuncion", "encarnacion", "ciudad-del-este"])
def test_output_name_matches_framework_aoi_path(city):
    """The tool saves <city>_<country>_aoi.geojson — the file the framework reads.

    pipeline.config derives ``aoi_path`` from ``city_normalized = "<city>_<country>"``
    (config.py:141) and both the framework (prepare_data.py) and the pipeline
    (sdg_stats_wrapper.py) require that exact file name.
    """
    cfg = load_config(city)
    tool_name = f"{normalize(city)}_{normalize(cfg.country)}_aoi.geojson"
    assert os.path.basename(cfg.aoi_path) == tool_name
    assert normalize(cfg.country) in tool_name


def test_norm_name_folds_case_accents_and_whitespace():
    assert _norm_name("Asunción") == "asuncion"
    assert _norm_name("CIUDAD DEL ESTE") == "ciudad del este"
    assert _norm_name("  Ciudad\tdel  Este  ") == "ciudad del este"
    assert _norm_name("Encarnación") == "encarnacion"


def test_first_fua_match_is_accent_and_case_insensitive():
    records = [
        {"eFUA_name": "Asunción", "eFUA_ID": 1},
        {"eFUA_name": "Encarnación", "eFUA_ID": 2},
        {"unknown": "x"},  # missing eFUA_name never matches
    ]
    assert _first_fua_match(records, ["encarnacion"]) == {"eFUA_name": "Encarnación", "eFUA_ID": 2}
    assert _first_fua_match(records, ["ASUNCION"]) == {"eFUA_name": "Asunción", "eFUA_ID": 1}
    assert _first_fua_match(records, ["somewhere"]) is None


def test_plausible_area_bounds_are_parameterized():
    big = _rect_polygon(-1, -1, 0.5, 0.5)  # ~28 000 km²
    assert not _plausible(big)  # default nominatim cap is 10 000 km²
    assert _plausible(big, max_area=FUA_MAX_AREA_KM2)
    small = _rect_polygon(-57.640, -25.280, -57.639, -25.279)
    assert not _plausible(small, max_area=FUA_MAX_AREA_KM2)


def test_main_default_source_is_nominatim(capsys):
    """Without --source, cities not in CITY_QUERIES are skipped, not fetched."""
    assert main(["--cities", "atlantis"]) == 1
    out = capsys.readouterr().out
    assert "no curated queries defined for 'atlantis'" in out


def test_main_fua_without_data_is_a_clear_error(capsys):
    """--source fua without an existing GeoPackage exits 2 before any download."""
    assert main(["--source", "fua", "--cities", "asuncion", "--fua-data", "/nonexistent/fua.gpkg"]) == 2
    err = capsys.readouterr().err
    assert "GHS-FUA data not found" in err