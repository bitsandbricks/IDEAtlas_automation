#!/usr/bin/env python3
"""Fetch city boundaries (AOIs) for the project cities.

Why this tool exists
--------------------
The ai-dua-mapping framework geocodes city names with Nominatim as part of its
data preparation, using the exact string it was given on the command line
(e.g. ``ciudad-del-este, paraguay``). That fails (or returns an ambiguous
match) for hyphenated names, so we pre-fetch the boundaries once with well
formed queries and place them where the framework expects them
(``ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson``). When a file
already exists there, the framework skips its own geocoding call.

Two boundary sources are supported (``--source``):

- ``nominatim`` (default): OpenStreetMap via Nominatim, using the curated
  ``CITY_QUERIES`` queries. Nominatim rate limits apply: the tool sleeps
  between requests and uses a custom User-Agent.
- ``fua``: the OECD/GHSL *Functional Urban Area* polygon from the global
  GHS-FUA GeoPackage (``GHS_FUA_UCDB2015_GLOBE_R2019A_54009_1K_V1_0.gpkg``,
  doi 10.2905/JRC.DEC76Y8, https://data.europa.eu/89h/347f0337-f2da-4592-87b3-e25975ec2c95).
  Cities match the dataset's ``eFUA_name`` attribute via ``CITY_FUA_NAMES``
  (case- and accent-insensitive). The polygon is reprojected from World
  Mollweide (EPSG:54009) to WGS84 so the output stays in the same CRS as the
  Nominatim path.

Run it inside the ``ideatlas`` conda environment:

    conda activate ideatlas
    make aois                        # Nominatim (default)
    make aois SOURCE=fua             # GHS-FUA (expects the gpkg under
                                     # ai-dua-mapping/data/raw/ghsl/fua/)
    make aois SOURCE=fua FUA_DATA=/path/to/GHS_FUA_UCDB2015_GLOBE_R2019A_54009_1K_V1_0.gpkg

Then verify the downloaded boundaries in a GIS viewer before running the
full pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

import requests

# Make `import pipeline.*` work when this script runs directly (as `make aois`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ideatlas-automation/0.1 (reproducible SDG 11.1.1 mapping)"
MIN_AREA_KM2 = 1.0
MAX_AREA_KM2 = 10000.0
# Functional Urban Areas are whole commuting/metro regions, far larger than a
# municipality, so they get a much higher area ceiling.
FUA_MAX_AREA_KM2 = 50000.0
DEFAULT_FUA_GEOJSON = "GHS_FUA_UCDB2015_GLOBE_R2019A_54009_1K_V1_0.gpkg"

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

# GHS-FUA names: values are the ``eFUA_name`` attribute values (the dataset's
# own English name for each Functional Urban Area) to match for the city. The
# first name that matches any FUA feature wins; matching is case- and
# accent-insensitive. Only consulted when --source fua.
CITY_FUA_NAMES: Dict[str, List[str]] = {
    "asuncion": ["Asunción"],
    "encarnacion": ["Encarnación"],
    "ciudad-del-este": ["Ciudad del Este"],
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


def _plausible(
    geometry: dict,
    min_area: float = MIN_AREA_KM2,
    max_area: float = MAX_AREA_KM2,
) -> bool:
    area = _area_km2(geometry)
    if area is None:
        return True  # unknown area; do not reject on that alone
    return min_area <= area <= max_area


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


def _norm_name(name: str) -> str:
    """Fold a place name for fuzzy matching: lowercase, strip accents, collapse spaces.

    ``"Asunción"``, ``"asuncion"`` and ``"  ASUNCIÓN  "`` all fold to
    ``"asuncion"`` so GHS-FUA ``eFUA_name`` values can be matched without
    worrying about the dataset's casing or diacritics.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(without_accents.strip().lower().split())


def _first_fua_match(records: List[dict], names: List[str]) -> Optional[dict]:
    """Return the first record whose ``eFUA_name`` matches one of ``names``.

    Matching is case- and accent-insensitive. ``names`` are tried in priority
    order against every record. Returns ``None`` when nothing matches.
    """
    for name in names:
        norm = _norm_name(name)
        for record in records:
            if _norm_name(str(record.get("eFUA_name", ""))) == norm:
                print(f"    matched eFUA_name={record.get('eFUA_name')!r} (alias {name!r})")
                return record
    print(f"    no GHS-FUA matched the given names ({', '.join(names)})")
    return None


def _reproject_to_wgs84(geometry: dict) -> Optional[dict]:
    """Reproject a GeoJSON geometry from World Mollweide (EPSG:54009) to WGS84.

    Returns ``None`` when rasterio is unavailable.
    """
    try:
        from rasterio.warp import transform_geom
    except ImportError:
        print("    rasterio not available; cannot reproject from EPSG:54009")
        return None
    return transform_geom("EPSG:54009", "EPSG:4326", geometry)


def fetch_fua_geometry(
    fua_data: str,
    names: List[str],
    max_area: float = FUA_MAX_AREA_KM2,
) -> Optional[dict]:
    """Return the WGS84 GeoJSON geometry of the GHS-FUA matching ``names``.

    Reads the global GHS-FUA GeoPackage (EPSG:54009, World Mollweide),
    selects the feature whose ``eFUA_name`` matches one of ``names``, and
    reprojects it to EPSG:4326 so the output keeps the same WGS84 contract as
    the Nominatim path. Requires fiona and rasterio (both present in the
    ``ideatlas`` environment).
    """
    if not os.path.exists(fua_data):
        print(f"    GHS-FUA data not found: {fua_data}")
        return None

    try:
        import fiona
    except ImportError:
        print("    fiona not available; cannot read the GeoPackage")
        return None

    print(f"    scanning GHS-FUA GeoPackage: {fua_data}")
    records: List[dict] = []
    geometries: Dict[int, dict] = {}
    try:
        with fiona.open(fua_data) as src:
            for index, feature in enumerate(src):
                records.append(feature.get("properties") or {})
                geometries[index] = feature.get("geometry")
    except Exception as exc:
        print(f"    could not read GHS-FUA data: {exc}")
        return None

    record = _first_fua_match(records, names)
    if record is None:
        return None
    index = records.index(record)
    geometry = geometries.get(index)
    if geometry is None:
        print("    matched feature has no geometry")
        return None

    geometry = _reproject_to_wgs84(geometry)
    if geometry is None:
        return None
    if not _plausible(geometry, max_area=max_area):
        print(f"    rejected (implausible area): {record['eFUA_name']}")
        return None
    return geometry


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
    parser.add_argument(
        "--source",
        choices=("nominatim", "fua"),
        default="nominatim",
        help="Boundary source: 'nominatim' (OSM, default) or 'fua' (GHS "
        "Functional Urban Areas, requires the GHS-FUA GeoPackage).",
    )
    parser.add_argument(
        "--fua-data",
        default=None,
        help="Path to the GHS-FUA GeoPackage "
        f"({DEFAULT_FUA_GEOJSON}). Required when --source fua; defaults to "
        "the conventional location under ai-dua-mapping/data/raw/ghsl/fua/.",
    )
    parser.add_argument(
        "--sleep", type=float, default=1.2,
        help="Seconds between Nominatim calls (only used with --source nominatim).",
    )
    args = parser.parse_args(argv)

    from pipeline.config import load_config, load_global

    cfg = load_global()
    out_dir = args.out or os.path.join(cfg.ai_dua_mapping_dir, "data", "raw", "aoi")

    is_fua = args.source == "fua"
    if is_fua:
        fua_data = args.fua_data or os.path.join(
            cfg.ai_dua_mapping_dir, "data", "raw", "ghsl", "fua", DEFAULT_FUA_GEOJSON
        )
        if not os.path.exists(fua_data):
            print(
                f"GHS-FUA data not found at {fua_data}. Download the GHS-FUA "
                f"R2019A GeoPackage (doi 10.2905/JRC.DEC76Y8) and pass it with "
                f"--fua-data <path>, or drop it at the path above.",
                file=sys.stderr,
            )
            return 2
    else:
        fua_data = None

    known_cities = CITY_FUA_NAMES if is_fua else CITY_QUERIES

    if args.cities:
        city_names = [c.strip().lower() for c in args.cities.split(",") if c.strip()]
    else:
        city_names = list(known_cities.keys())

    for unknown in set(city_names) - set(known_cities):
        what = "GHS-FUA names" if is_fua else "curated queries"
        print(f"WARNING: no {what} defined for {unknown!r}; skipping.")
    city_names = [c for c in city_names if c in known_cities]
    if not city_names:
        print("No city names to process.", file=sys.stderr)
        return 1

    os.makedirs(out_dir, exist_ok=True)
    session = requests.Session() if not is_fua else None

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
        if is_fua:
            geometry = fetch_fua_geometry(fua_data, CITY_FUA_NAMES[city])
        else:
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