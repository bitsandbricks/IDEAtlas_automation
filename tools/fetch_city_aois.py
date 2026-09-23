#!/usr/bin/env python3
"""Fetch city boundaries (AOIs) for the project cities from OSM.

Why this tool exists
--------------------
The ai-dua-mapping framework geocodes city names with Nominatim as part of its
data preparation, using the exact string it was given on the command line
(e.g. ``ciudad-del-este, paraguay``). That fails (or returns an ambiguous
match) for hyphenated names, so we pre-fetch the boundaries once with well
formed queries and place them where the framework expects them
(``ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson``). When a file
already exists there, the framework skips its own geocoding call.

Nominatim rate limits apply: this tool sleeps between requests and uses a
custom User-Agent. Run it inside the ``ideatlas`` conda environment:

    conda activate ideatlas
    make aois

Then verify the downloaded boundaries in a GIS viewer before running the
full pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

# Make `import pipeline.*` work when this script runs directly (as `make aois`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ideatlas-automation/0.1 (reproducible SDG 11.1.1 mapping)"
MIN_AREA_KM2 = 1.0
MAX_AREA_KM2 = 10000.0

# Curated queries: try them in order until a plausible polygon is returned.
# The entries below are the example cities from the first implementation
# (Paraguay); add your own cities/regions here. Unknown city names are
# skipped with a warning (then provide the AOI file manually).
CITY_QUERIES: Dict[str, List[str]] = {
    "asuncion": [
        "Distrito Capital, Paraguay",
        "Asuncion, Paraguay",
        "Asuncion",
    ],
    "encarnacion": [
        "Encarnacion, Itapua, Paraguay",
        "Encarnacion, Paraguay",
    ],
    "ciudad-del-este": [
        "Ciudad Del Este, Alto Parana, Paraguay",
        "Ciudad Del Este, Paraguay",
    ],
}


def _area_km2(geometry: dict) -> Optional[float]:
    """Approximate polygon area in km2 from lon/lat coordinates.

    Returns ``None`` when the area cannot be computed (e.g. shapely missing);
    callers treat that as "plausible" (never reject on unknown area).
    """
    try:
        from shapely.geometry import shape
    except ImportError:
        return None

    geom = shape(geometry)
    if geom.is_empty or geom.area == 0:
        return 0.0
    return geom.area * (111.32 ** 2)  # rough degrees^2 -> km^2


def _plausible(geometry: dict) -> bool:
    area = _area_km2(geometry)
    if area is None:
        return True  # unknown area; do not reject on that alone
    return MIN_AREA_KM2 <= area <= MAX_AREA_KM2


def _plausible(geometry: dict) -> bool:
    area = _area_km2(geometry)
    return MIN_AREA_KM2 <= area <= MAX_AREA_KM2


def _nominatim_query(session: requests.Session, query: str, sleep: float) -> List[dict]:
    time.sleep(sleep)
    response = session.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "polygon_geojson": 1,
            "addressdetails": 1,
            "limit": 10,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _bbox_geometry(result: dict) -> Optional[dict]:
    """Build a WGS84 rectangle Polygon from a Nominatim result's boundingbox.

    Nominatim frequently returns capital cities as Points (their centre node);
    the ``boundingbox`` is the only area clue. Returns ``None`` when no usable
    bounding box is present.
    """
    bbox = result.get("boundingbox") or []
    if len(bbox) != 4:
        return None
    south, north, west, east = (float(value) for value in bbox)
    if not (south < north and west < east):
        return None
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


def fetch_aoi_geometry(
    session: requests.Session,
    queries: List[str],
    sleep: float = 1.2,
) -> Optional[dict]:
    """Return the GeoJSON geometry for the first plausible polygon found.

    When a query only returns Points (typical for capitals), the rectangle
    built from the first result's ``boundingbox`` is remembered and used as a
    last resort so the pipeline can still proceed; such AOIs should still be
    verified in a GIS viewer.
    """
    fallbacks: List[tuple[str, dict]] = []
    for index, query in enumerate(queries):
        print(f"  query[{index}]: {query}")
        try:
            results = _nominatim_query(session, query, sleep)
        except requests.RequestException as exc:
            print(f"    request failed: {exc}")
            continue

        polygons = [
            res for res in results
            if res.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")
        ]
        if not polygons and not fallbacks:
            for res in results:
                if res.get("geojson", {}).get("type") == "Point" and res.get("boundingbox"):
                    rectangle = _bbox_geometry(res)
                    if rectangle is not None:
                        fallbacks.append((res.get("display_name", "?"), rectangle))
                        break
        if not polygons:
            print("    no polygon result from Nominatim")
            continue

        for candidate in polygons:
            geometry = candidate["geojson"]
            name = candidate.get("display_name", "?")
            if _plausible(geometry):
                print(f"    selected: {name}")
                return geometry
            print(f"    rejected (implausible area): {name}")

    if fallbacks:
        name, geometry = fallbacks[0]
        print(f"    using rectangular fallback from boundingbox of: {name}")
        print("    WARNING: verify this AOI in a GIS viewer before running the pipeline.")
        if _plausible(geometry):
            return geometry
        print(f"    rejected (implausible area): {name}")

    print("    no polygon result from Nominatim")
    return None


def build_aoi_feature(geometry: dict) -> dict:
    """Wrap a bare geometry into a FeatureCollection (safest for geopandas)."""
    return {
        "type": "FeatureCollection",
        "name": "AOI",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [{"type": "Feature", "properties": {}, "geometry": geometry}],
    }


def normalize(city: str) -> str:
    return city.strip().lower().replace(" ", "_").replace("-", "_")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=None,
        help="Output folder for the <city>_<country>_aoi.geojson files.",
    )
    parser.add_argument(
        "--cities",
        default="",
        help="Comma-separated subset of city names (default: all project cities).",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="Country part of the file names, e.g. 'brazil'. Defaults to each "
        "city's 'country' in config/cities/<city>.yaml.",
    )
    parser.add_argument("--sleep", type=float, default=1.2, help="Seconds between Nominatim calls.")
    args = parser.parse_args(argv)

    from pipeline.config import load_config, load_global

    cfg = load_global()
    out_dir = args.out or os.path.join(cfg.ai_dua_mapping_dir, "data", "raw", "aoi")

    if args.cities:
        city_names = [c.strip().lower() for c in args.cities.split(",") if c.strip()]
    else:
        city_names = list(CITY_QUERIES.keys())

    for unknown in set(city_names) - set(CITY_QUERIES):
        print(f"WARNING: no curated queries defined for {unknown!r}; skipping.")
    city_names = [c for c in city_names if c in CITY_QUERIES]
    if not city_names:
        print("No city names to process.", file=sys.stderr)
        return 1

    os.makedirs(out_dir, exist_ok=True)
    session = requests.Session()

    failures: List[str] = []
    for city in city_names:
        country = args.country or load_config(city).country
        country_norm = normalize(country)
        file_name = f"{normalize(city)}_{country_norm}_aoi.geojson"
        target = os.path.join(out_dir, file_name)

        if os.path.exists(target):
            print(f"SKIP {city}: {target} already exists. Remove it to re-download.")
            continue

        print(f"\n=== {city} ===")
        geometry = fetch_aoi_geometry(session, CITY_QUERIES[city], sleep=args.sleep)
        if geometry is None:
            msg = (
                f"Could not fetch a plausible boundary for {city}. "
                f"Provide one manually at {target} (WGS84)."
            )
            print(msg, file=sys.stderr)
            failures.append(city)
            continue

        with open(target, "w", encoding="utf-8") as fh:
            json.dump(build_aoi_feature(geometry), fh, ensure_ascii=False)
        print(f"Saved {target}")

    print("\nSummary:")
    print(f"  saved in: {out_dir}")
    if failures:
        print(f"  FAILED: {', '.join(failures)} (provide AOI files manually)")
        return 1
    print("  All AOIs resolved. You may now run 'make city CITY=<city>'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())