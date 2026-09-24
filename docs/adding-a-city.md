# Adding a new city (or AOI of interest)

This guide explains exactly how to run the automation layer for a city that is
not in the default example set (Asunción, Encarnación, Ciudad del Este).

Everything the layer needs to know about a city lives in **two places**, plus
one **optional** file:

| What | Where | Purpose |
|---|---|---|
| `config/cities/<city>.yaml` | required | Who/where: `country`, `year` |
| `CITY_QUERIES` or `CITY_FUA_NAMES` entry in `tools/fetch_city_aois.py` | recommended | How to find the city boundary (Nominatim queries, or a GHS-FUA name) |
| `ai-dua-mapping/data/raw/reference_data/<city>_<country>_reference_<year>_v<n>.geojson` | only for `finetune`/`train` | Labeled "deprived urban area" polygons for training (version suffix required — see [§5](#5-fine-tuning-or-training-a-local-model)) |

Everything else (file names, downloads, the report) is derived automatically
from the city key and country — you never touch the `ai-dua-mapping`
framework, which is pinned and left untouched.

---

## 0. Decide the three identifiers

Copy this table and fill it in:

| Identifier | Rules | Example |
|---|---|---|
| `city` | lowercase a–z, spaces become underscores; hyphens are allowed but also become underscores in file names. Must be **unique across the repo** because it prefixes every file. | `rio-de-janeiro` |
| `country` | lowercase, no spaces/separators. **Required** — it is embedded in every framework file name. | `brazil` |
| `year` | references files are year-stamped; default `2025`. | `2025` |
| `task` | `classify` (default), `finetune`, or `train`. | `classify` |

> **Uniqueness matters.** Two same-named cities in the *same* country collide
> on every file (`springfield_usa_aoi.geojson`). Disambiguate the city key
> (`springfield-il` / `springfield-mo`, `newcastle-uk` / `newcastle-au`) or
> use a locally distinct name.

---

## 1. Drop the city config

Create `config/cities/<city>.yaml`:

```yaml
# Rio de Janeiro (Rio de Janeiro state).
country: brazil
year: 2025        # optional — defaults to 2025
```

This file is merged over `config/base.yaml` (model `mbcnn`, retry policy, log /
outputs paths). Per-city keys win. You can also override `country` / `year` /
`task` per run on the command line — the merged result is:
`<city>_<country>` normalized, e.g. `rio_de_janeiro_brazil`.

---

## 2. Make the boundary resolvable

Three options; pick one.

### Option A (recommended): add curated queries

Edit `tools/fetch_city_aois.py`, in the `CITY_QUERIES` dict. The key must match
your `city` exactly (lowercase). List queries from most to least specific —
they are tried in order and the **first plausible polygon wins**:

```python
CITY_QUERIES: Dict[str, List[str]] = {
    # ... existing example cities ...
    "rio-de-janeiro": [
        "Rio de Janeiro, Rio de Janeiro, Brazil",
        "Rio de Janeiro, Brazil",
    ],
}
```

Why this exists: the framework geocodes city names itself via Nominatim, using
the exact string it was given. Hyphenated names (`ciudad-del-este`) are
ambiguous there, so this layer pre-downloads the boundary once with
well-formulated queries.

Notes on the matching logic:

- Plausibility filter: the polygon must have an area between 1 km² and
  10,000 km²; implausible candidates are rejected and the next query is tried.
- **Capitals often come back as Points.** Nominatim ranks a capital's centre
  *node* above its boundary *relation*, so queries like `"Asuncion, Paraguay"`
  yield a `Point`, not a polygon. When that happens: reword the query to hit
  the administrative boundary instead — e.g. for Asunción the working query is
  `"Distrito Capital, Paraguay"`. As a last resort the tool falls back to a
  rectangle built from Nominatim's bounding box (logged as
  `using rectangular fallback ...`) — **verify such AOIs in a GIS viewer**
  before running the pipeline.
- Nominatim is rate-limited: the tool sleeps (`--sleep`, default 1.2 s) and
  sends a custom User-Agent. Be gentle on repeat runs.
- A city key with **no queries** is skipped with a
  `WARNING: no curated queries defined for ...; skipping` message — that is
  your signal to either add queries or use Option B.

### Option B: provide the AOI yourself

If `ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson` already exists,
the framework **skips its own geocoding** and uses your file verbatim. This is
the right path when you only want to map a custom polygon (e.g. an official
municipal boundary, a neighbourhood, or a study region that is not a "city").

Requirements:

- WGS84 (`EPSG:4326` / longitude, latitude).
- A single `Polygon` or `MultiPolygon`.
- A GeoJSON `FeatureCollection` (that is what `build_aoi_feature` writes;
  copy it as your template).

The AOI is what determines the download window (Sentinel-2 tile, building
footprints, GHSL pixels), so size/placement matters — a sliver or a
mis-projected file produces worthless data.

### Option C: GHS Functional Urban Area (GHS-FUA)

Use the OECD/GHSL *Functional Urban Area* polygon — the commuting/metro
region around a city — instead of a Nominatim administrative boundary. This
is the right choice when the "city of interest" is really a metropolitan
area and not a municipality.

1. **Download the global GeoPackage once** (the automation layer caches
   nothing itself):
   - Product: `GHS-FUA R2019A` (doi `10.2905/JRC.DEC76Y8`) — dataset page
     https://data.europa.eu/89h/347f0337-f2da-4592-87b3-e25975ec2c95
   - Unzip the `GHS_FUA_UCDB2015_GLOBE_R2019A_54009_1K_V1_0.gpkg` and place it
     at `ai-dua-mapping/data/raw/ghsl/fua/` (this path is the default when
     `FUA_DATA` is not given).
2. **Add the matching `eFUA_name`** (the dataset's English name for the FUA,
   with accents) to the `CITY_FUA_NAMES` dict in `tools/fetch_city_aois.py`,
   keyed by your city. Matching is case- and accent-insensitive, so
   `"Asuncion"` finds `"Asunción"`:
   ```python
   CITY_FUA_NAMES: Dict[str, List[str]] = {
       # ... existing example cities ...
       "rio-de-janeiro": ["Rio de Janeiro"],
   }
   ```
   The tool reprojects the polygon from World Mollweide (EPSG:54009) to WGS84
   automatically (no QGIS step needed).
3. **Fetch:**
   ```bash
   make aois SOURCE=fua
   make aois SOURCE=fua FUA_DATA=/path/to/GHS_FUA_UCDB2015_GLOBE_R2019A_54009_1K_V1_0.gpkg
   ```

Notes:

- The dataset is a **2015** delineation at 1 km resolution; the AOI only sets
  the mapping window, so the vintage mismatch with the (e.g. 2025) imagery is
  irrelevant in practice.
- FUAs are large (up to tens of thousands of km²), so the area-plausibility
  cap for this source is relaxed to 50,000 km².
- Boundary fetched by `eFUA_name`; provide a name the dataset actually uses.
  If nothing matches you get `no GHS-FUA matched the given names ...`.

---

## 3. Fetch and verify the boundary

```bash
conda activate ideatlas      # (or install first: make setup)
make aois                                    # Nominatim (Option A)
make aois SOURCE=fua [FUA_DATA=<gpkg>]       # GHS-FUA (Option C)
```

`make aois` fetches AOIs for **every** city that has curated queries (or
GHS-FUA names) and skips files that already exist. For your new city this
produces `ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson`.

**Open it in a GIS viewer (QGIS) before running the pipeline** — a wrong
boundary means gigabytes of wasted downloads. To re-download:
`rm ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson` and run
`make aois` again.

If you used Option B, skip this step — the file is already in place.

---

## 4. Run the city

```bash
make city CITY=<city>                    # classify → statistics, no reference data needed
make city CITY=<city> TASK=finetune      # fine-tune on your reference data
make city CITY=<city> TASK=train         # train from scratch
make city CITY=<city> YEAR=2024          # different reference/download year
make city CITY=<city> WEIGHTS=/path/to/weights.h5   # use specific initial weights
```

What `make city` does, end to end:

1. Decides the effective task — if you ask for `finetune`/`train` but no
   reference data exists, it transparently falls back to `classify` (the
   reason is logged and stored in `data/processed/<city>_<year>_task_mode.json`).
2. Runs the real IDEAtlas framework:
   - downloads Sentinel-2 imagery for your AOI (`data/raw/sentinel/<city>_<country>/S2_<year>.tif`),
   - downloads building footprints (Google Open Buildings) and computes density,
   - downloads GHSL built-up/population rasters,
   - classifies (with the framework's pre-trained **global model**
     `ai-dua-mapping/checkpoint/global.s2.bd.mbcnn.weights.h5`) or trains /
     fine-tunes a local city model first,
   - writes the classified raster to `ai-dua-mapping/output/`.
3. Computes the SDG 11.1.1 statistics into
   `outputs/<city>_<year>_sdg_stats.json`.
4. Logs everything under `log/`.

Transient download/server failures are retried (default: 3 attempts with 30 s
backoff) before the pipeline stops.

> **First run downloads a lot** (Sentinel-2 ~1 GB plus GHSL tiles): you need
> internet and disk space. Subsequent runs reuse the cached files.

---

## 5. Fine-tuning or training a local model

Optional. Only needed when you have **your own labeled reference data** —
polygons marking "deprived urban areas" (DUAs) in your city — and want a model
tuned to it.

### The reference file you provide

Drop a GeoJSON into `ai-dua-mapping/data/raw/reference_data/`, named with a
**version suffix**:

```
<city>_<country>_reference_<year>_v1.geojson
# e.g. rio_de_janeiro_brazil_reference_2020_v1.geojson
```

> **Version suffix is required.** The framework parses the version from
> everything after the last `_v` in the file name (`prepare_data.py`). A name
> **without** `_vN` (e.g. `..._reference_2020.geojson`) makes that parser build
> a broken output path. Always use `_vN`, and bump `N` when you update the
> polygons.

Expected format (read by geopandas, `preprocessing/create_ref.py`):

| Property | Requirement |
|---|---|
| File | GeoJSON (`FeatureCollection`) |
| Geometry | `Polygon` / `MultiPolygon` features |
| Attributes | **None required** — every feature becomes a DUA. There is no class/label column; the layer assigns class 2 to all of them. |
| CRS | Must be declared, e.g. WGS84 (`EPSG:4326`) as in the sample below. Auto-reprojected to the Sentinel-2 raster CRS if different. |
| Content | At least one polygon. An empty feature set silently yields an all-"built-up" mask (no DUAs) — a useless model. |
| Placement | Local deprived blocks inside/near your AOI, **not** the whole city polygon. |

A minimal working sample (the exact shape the test fixtures use):

```json
{
  "type": "FeatureCollection",
  "name": "dua_reference",
  "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
  "features": [
    {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[
          [-56.4520, -25.4520],
          [-56.4280, -25.4520],
          [-56.4280, -25.4280],
          [-56.4520, -25.4280],
          [-56.4520, -25.4520]
        ]]
      }
    }
  ]
}
```

### What the pipeline does with it

`prepare_data.py` rasterizes your polygons into a 3-class label mask aligned
to the Sentinel-2 grid (same CRS, resolution, extent) and saves it as
`<city>_<country>_reference_<year>_v<version>.tif`:

- `0` — non built-up;
- `1` — built-up (auto-derived: GHSL built-up fraction > 15);
- `2` — your DUAs.

The mask is then divided into training tiles (`..._v<version>_clipped.tif`)
and paired with the matching Sentinel-2 and building-density patches.

**Alternative — provide the mask directly:** if a file matching
`<city>_<country>_reference_<year>_v*.tif` already exists, `prepare_data.py`
reuses it and skips polygon processing (`prepare_data.py`, step 4). You can
pre-build such a mask yourself (uint8, values 0/1/2, on the Sentinel-2 grid).

Then run:

```bash
make city CITY=<city> TASK=finetune
```

The pipeline detects the reference data, trains a city model, and saves it to
`ai-dua-mapping/checkpoint/<city>_<country>.s2.bd.mbcnn.weights.h5`, then
classifies with it automatically. Without reference data the `finetune`
request falls back to `classify`.

---

## 6. Multiple cities

Run several cities at once with a `city:country` list. The `:country` part is
for legibility — make it match the `country` in each city's YAML:

```bash
make all-cities                        # the default example set
make all-cities CITIES=rio:brazil,lima:peru   # your own set
make all-cities PARALLEL=1             # same set, cities in parallel
```

To change the default set permanently, edit `CITIES` near the top of the
`Makefile`.

---

## 7. Naming convention reference

All names follow `<city>_<country>` normalized (lowercase; spaces, commas and
hyphens → underscores). The layer and the framework agree on every path:

| Output | Location |
|---|---|
| City boundary (AOI) | `ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson` |
| Sentinel-2 tile | `ai-dua-mapping/data/raw/sentinel/<city>_<country>/S2_<year>.tif` |
| Building footprints | `ai-dua-mapping/data/raw/buildings/<city>_<country>_bldg.gpkg` |
| Building density | `ai-dua-mapping/data/raw/buildings/density/<city>_<country>_bd.tif` |
| GHSL rasters | `ai-dua-mapping/data/raw/ghsl/built|pop/<PREFIX>_*.tif` |
| Reference data (finetune/train) | `ai-dua-mapping/data/raw/reference_data/<city>_<country>_reference_<year>_v<n>.geojson` (or a pre-built `..._v<n>.tif` mask) |
| Classified raster | `ai-dua-mapping/output/<city>_<country>.s2.bd.<model>.<year>.tif` |
| City model weights | `ai-dua-mapping/checkpoint/<city>_<country>.s2.bd.<model>.weights.h5` |
| SDG 11.1.1 statistics | `outputs/<city>_<year>_sdg_stats.json` |
| Task decision (fallback info) | `data/processed/<city>_<year>_task_mode.json` |
| Consolidated report | `outputs/ideatlas_report.md` / `.xlsx` |

`<model>` defaults to `mbcnn`. `<PREFIX>` is the **first three letters**
(uppercase) of `<city>_<country>`: `rio_de_janeiro_brazil` → `RIO`. Do **not**
rename the downloaded GHSL rasters — the framework relies on that prefix to
find them.

---

## 8. Pitfalls / FAQ

- **Empty `country`** → file names become `<city>__aoi.geojson`,
  `<city>__...` everywhere. Always set it.
- **Colliding city keys** → one overwrites the other's files. Keep keys unique.
- **Renamed GHSL/S2 files** → the framework matches by prefix/pattern and will
  not find them. Leave downloads in place.
- **No curated query and no manual AOI** → the run fails at data preparation
  with instructions to provide
  `data/raw/aoi/<city>_<country>_aoi.geojson` in WGS84 (see
  [step 2](#2-make-the-boundary-resolvable)).
- **Reference file without `_vN` suffix** → the framework builds a broken
  output path. Always name it `..._reference_<year>_v1.geojson` (see
  [§5](#5-fine-tuning-or-training-a-local-model)).
- **AOI looks wrong** → verify in QGIS *before* `make city`; a bad polygon
  means wasted downloads.
- **Capital returned as a Point / "no polygon result"** → rewrite the query to
  target the administrative boundary (see [§2](#2-make-the-boundary-resolvable))
  or provide the AOI manually; a rectangular `boundingbox` fallback is used
  only as a last resort and must be verified.
- **Rate-limited by Nominatim** → increase the inter-request delay
  (`tools/fetch_city_aois.py --sleep`).
- **`WARNING: no curated queries defined`** → add your queries or supply the
  AOI file manually.

---

## 9. Checklist

- [ ] `config/cities/<city>.yaml` exists and has a `country`.
- [ ] City key is unique repo-wide; names make sense for `<city>_<country>`.
- [ ] Either queries added to `CITY_QUERIES` (Nominatim), a GHS-FUA name in
      `CITY_FUA_NAMES` + `SOURCE=fua` (GHS-FUA), or a hand-written AOI file is
      in place.
- [ ] `make aois` ran and the boundary opens correctly in a GIS viewer.
- [ ] (finetune/train) reference polygons named
      `<city>_<country>_reference_<year>_v1.geojson` (version suffix required)
      sit in `reference_data/`, with at least one polygon.
- [ ] `make city CITY=<city>` completes and
      `outputs/<city>_<year>_sdg_stats.json` exists.
- [ ] `make report` includes the new city sheet.