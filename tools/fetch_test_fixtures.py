#!/usr/bin/env python3
"""Generate the input fixtures for the synthetic test cities (development only).

Why this tool exists
--------------------
Running the pipeline against the *real* framework requires real input data.
For the automated end-to-end test we use a fictional city name
(``testcity`` / ``testcity-ref``) but this city still needs valid Sentinel-2,
building footprints, building density and GHSL data ... so we generate it by
calling the very same framework download functions on a small *real* area
(a few square kilometres; default: a square near Coronel Oviedo, Paraguay).
The outputs are written exactly where ``preprocessing/prepare_data.py``
expects them, under ``ai-dua-mapping/data/raw/``.

It also copies small sample copies into ``test_fixtures/`` so the fixture
set can, optionally, be versioned for reproducible tests. The expected SDG
statistics file is NOT generated here: run the pipeline once on ``testcity``
and freeze its ``outputs/testcity_2025_sdg_stats.json`` as
``test_fixtures/expected_sdg_stats_testcity.json`` (see tests/e2e).

Usage
-----
Run inside the ``ideatlas`` conda environment (network required, one time):

    conda activate ideatlas
    make fixtures

This is tooling for developers and for the test suite; it is NOT part of the
production pipeline.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
FRAMEWORK_DIR = REPO_ROOT / "ai-dua-mapping"

# Default: ~6.6 x 6.6 km square centred near Coronel Oviedo, Paraguay.
DEFAULT_BBOX = "-56.4700,-25.4700,-56.4100,-25.4100"
YEAR = 2025


def parse_bbox(text: str) -> List[float]:
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be min_lon,min_lat,max_lon,max_lat")
    min_lon, min_lat, max_lon, max_lat = parts
    if not (min_lon < max_lon and min_lat < max_lat):
        raise ValueError("Invalid bbox: min values must be smaller than max values.")
    return parts


def write_aoi(bbox: List[float], city_norm: str, base: Path) -> Path:
    """Write a WGS84 square AOI for a city/namespace under base/aoi/."""
    min_lon, min_lat, max_lon, max_lat = bbox
    polygon = {
        "type": "FeatureCollection",
        "name": "AOI",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [min_lon, min_lat],
                        [max_lon, min_lat],
                        [max_lon, max_lat],
                        [min_lon, max_lat],
                        [min_lon, min_lat],
                    ]],
                },
            }
        ],
    }
    target = base / "aoi" / f"{city_norm}_aoi.geojson"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(polygon, fh, ensure_ascii=False)
    print(f"  wrote AOI: {target}")
    return target


def fetch_sentinel(city_norm: str, base: Path, year: int, force: bool) -> Path:
    target = base / "sentinel" / city_norm / f"S2_{year}.tif"
    if target.exists() and not force:
        print(f"  SKIP S2: {target} exists")
        return target
    from preprocessing import stac_api  # late import (needs framework env)

    print("  downloading Sentinel-2 imagery ...")
    path = stac_api.get_s2(city_norm, year=year, basedir=str(base))
    print(f"  Sentinel-2 done: {path}")
    return Path(path)


def fetch_buildings(city_norm: str, base: Path, aoi: Path, force: bool) -> Path:
    target = base / "buildings" / f"{city_norm}_bldg.gpkg"
    if target.exists() and not force:
        print(f"  SKIP buildings: {target} exists")
        return target
    from preprocessing import google_buildings  # late import

    # The framework's google_buildings downloader does not create its own
    # output directory (prepare_data.py does that in the normal pipeline).
    (base / "buildings").mkdir(parents=True, exist_ok=True)
    print("  downloading building footprints (Google Open Buildings) ...")
    result = google_buildings.download_google_open_buildings(
        aoi_path=str(aoi),
        output_path=str(base / "buildings" / f"{city_norm}_bldg"),
        format_type="gpkg",
    )
    if not result or not os.path.exists(result):
        raise RuntimeError(
            f"No buildings found for the AOI ({aoi}). Pick a more built-up bbox."
        )
    print(f"  Building footprints done: {result}")
    return Path(result)


def fetch_ghsl_data(base: Path, aoi: Path, force: bool) -> None:
    existing_built = glob.glob(str(base / "ghsl" / "built" / "*.tif"))
    existing_pop = glob.glob(str(base / "ghsl" / "pop" / "*.tif"))
    if not force and existing_built and existing_pop:
        print("  SKIP GHSL: built and pop tiles already present")
        return

    from preprocessing import fetch_ghsl  # late import

    # Ensure the GHSL root exists (the downloader creates its own sub-folders).
    (base / "ghsl").mkdir(parents=True, exist_ok=True)
    print("  downloading GHSL data (built-up and population tiles) ...")
    downloader = fetch_ghsl.GHSLDownloader(temp_dir=str(base / "ghsl" / "temp"))
    downloader.download_tiles(
        aoi_geojson=str(aoi),
        output_dir=str(base / "ghsl"),
        data_type="pop, built",
    )
    print("  GHSL data done.")


def build_density(city_norm: str, base: Path, s2: Path, buildings: Path, force: bool) -> Path:
    target = base / "buildings" / "density" / f"{city_norm}_bd.tif"
    if target.exists() and not force:
        print(f"  SKIP density: {target} exists")
        return target
    from preprocessing import create_density  # late import

    # CreateDensity does not create its own output directory either.
    (base / "buildings" / "density").mkdir(parents=True, exist_ok=True)
    print("  computing building density raster ...")
    result = create_density.CreateDensity(str(s2), str(buildings), str(target))
    print(f"  Building density done: {result}")
    return Path(result)


def write_reference(city_norm: str, base: Path, year: int, force: bool) -> Path:
    """Write a synthetic DUA-reference polygon for a test city."""
    target = base / "reference_data" / f"{city_norm}_reference_{year}.geojson"
    if target.exists() and not force:
        print(f"  SKIP reference: {target} exists")
        return target

    # A rectangle covering the central ~40% of the (small) AOI.
    feature = {
        "type": "FeatureCollection",
        "name": "test_reference",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"class": "DUA", "source": "synthetic-test"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [-56.4520, -25.4520],
                        [-56.4280, -25.4520],
                        [-56.4280, -25.4280],
                        [-56.4520, -25.4280],
                        [-56.4520, -25.4520],
                    ]],
                },
            }
        ],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(feature, fh, ensure_ascii=False)
    print(f"  wrote reference: {target}")
    return target


def copy_to_fixtures(base: Path, s2: Path, buildings: Path, ghsl_sample: Path, reference: Path) -> None:
    """Copy small sample copies into the versioned test_fixtures/ folder."""
    fixtures_dir = REPO_ROOT / "test_fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    copies = [
        (base / "aoi" / "testcity_testland_aoi.geojson", "aoi_sample.geojson"),
        (s2, "s2_tile_sample.tif"),
        (buildings, "footprints_sample.gpkg"),
        (ghsl_sample, "ghsl_sample.tif"),
        (reference, "reference_sample_testcity-ref.geojson"),
    ]
    for src, name in copies:
        dst = fixtures_dir / name
        if dst.exists():
            continue
        shutil.copyfile(src, dst)
        print(f"  copied {name} into test_fixtures/")
    if not (fixtures_dir / "expected_sdg_stats_testcity.json").exists():
        print(
            "\nNote: freeze the expected stats after the first successful testcity run:\n"
            "  cp outputs/testcity_2025_sdg_stats.json "
            "test_fixtures/expected_sdg_stats_testcity.json"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        default=DEFAULT_BBOX,
        help=f"min_lon,min_lat,max_lon,max_lat of the real area used (default {DEFAULT_BBOX}).",
    )
    parser.add_argument("--year", type=int, default=YEAR, help="Processing year (default 2025).")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download/re-compute files that already exist.",
    )
    args = parser.parse_args(argv)

    bbox = parse_bbox(args.bbox)
    base = FRAMEWORK_DIR / "data" / "raw"
    base.mkdir(parents=True, exist_ok=True)

    # Change into the framework dir so its relative config/imports work.
    os.chdir(FRAMEWORK_DIR)
    sys.path.insert(0, str(FRAMEWORK_DIR))

    print("Step 1/4: testcity input data (classification fixtures)")
    testcity = "testcity_testland"
    aoi = write_aoi(bbox, testcity, base)
    s2 = fetch_sentinel(testcity, base, args.year, args.force)
    buildings = fetch_buildings(testcity, base, aoi, args.force)
    fetch_ghsl_data(base, aoi, args.force)
    density = build_density(testcity, base, s2, buildings, args.force)

    print("Step 2/4: testcity-ref (fine-tuning fixtures, reusing the same area)")
    ref_city = "testcity_ref_testland"
    write_aoi(bbox, ref_city, base)
    for name, src_path in (
        ("sentinel", s2),
        ("bldg", buildings),
        ("bd", density),
    ):
        if name == "sentinel":
            dst = base / "sentinel" / ref_city / f"S2_{args.year}.tif"
        elif name == "bldg":
            dst = base / "buildings" / f"{ref_city}_bldg.gpkg"
        else:
            dst = base / "buildings" / "density" / f"{ref_city}_bd.tif"
        if not dst.exists() or args.force:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_path, dst)
            print(f"  copied {dst.name} for {ref_city}")
    reference = write_reference(ref_city, base, args.year, args.force)

    print("Step 3/4: copy sample files into test_fixtures/")
    ghsl_built = sorted(glob.glob(str(base / "ghsl" / "built" / "*.tif")))
    ghsl_sample = Path(ghsl_built[0]) if ghsl_built else None
    if ghsl_sample is None:
        print("  WARNING: no GHSL built-up tile found; skipping sample copy.")
    else:
        copy_to_fixtures(base, s2, buildings, ghsl_sample, reference)

    print("Step 4/4: verify fixture paths the framework expects")
    required = [
        base / "aoi" / f"{testcity}_aoi.geojson",
        base / "sentinel" / testcity / f"S2_{args.year}.tif",
        base / "buildings" / f"{testcity}_bldg.gpkg",
        base / "buildings" / "density" / f"{testcity}_bd.tif",
        base / "aoi" / f"{ref_city}_aoi.geojson",
        base / "sentinel" / ref_city / f"S2_{args.year}.tif",
        base / "buildings" / f"{ref_city}_bldg.gpkg",
        base / "buildings" / "density" / f"{ref_city}_bd.tif",
        base / "reference_data" / f"{ref_city}_reference_{args.year}.geojson",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        for path in missing:
            print(f"  MISSING: {path}")
        print("Fixture generation incomplete; fix the reported steps.", file=sys.stderr)
        return 1

    print("\nAll fixtures are in place. You can now run:")
    print("  make city CITY=testcity TASK=classify")
    print("  make city CITY=testcity-ref TASK=finetune")
    return 0


if __name__ == "__main__":
    sys.exit(main())