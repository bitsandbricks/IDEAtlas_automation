# Adding a new city (or AOI of interest)

This guide explains exactly how to run the automation layer for a city that is
not in the default example set (Asunción, Encarnación, Ciudad del Este).

Everything the layer needs to know about a city lives in **two places**, plus
one **optional** file:

| What | Where | Purpose |
|---|---|---|
| `config/cities/<city>.yaml` | required | Who/where: `country`, `year` |
| `CITY_QUERIES` entry in `tools/fetch_city_aois.py` | recommended | How to find the city boundary (Nominatim queries) |
| `ai-dua-mapping/data/raw/reference_data/<city>_<country>_reference_<year>.geojson` | only for `finetune`/`train` | Labeled "deprived urban area" polygons for training |

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

Two options; pick one.

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

---

## 3. Fetch and verify the boundary

```bash
conda activate ideatlas      # (or install first: make setup)
make aois
```

`make aois` fetches AOIs for **every** city that has curated queries and
skips files that already exist. For your new city this produces
`ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson`.

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

Optional. Only needed when you have **your own labeled reference data**
(polygons marking "deprived urban areas") and want a model tuned to your city.

Place the reference file in `ai-dua-mapping/data/raw/reference_data/`:

```
<city>_<country>_reference_<year>.geojson
# e.g. rio_de_janeiro_brazil_reference_2020.geojson
```

Variants accepted by the framework: `.tif` rasters, and version suffixes
(`..._reference_2020_v1.geojson`, `_v2`, ...).

Then run `make city CITY=<city> TASK=finetune`. The pipeline detects the
reference data, trains a city model, and saves it to
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
| Reference data (finetune/train) | `ai-dua-mapping/data/raw/reference_data/<city>_<country>_reference_<year>.geojson` |
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
- **AOI looks wrong** → verify in QGIS *before* `make city`; a bad polygon
  means wasted downloads.
- **Rate-limited by Nominatim** → increase the inter-request delay
  (`tools/fetch_city_aois.py --sleep`).
- **`WARNING: no curated queries defined`** → add your queries or supply the
  AOI file manually.

---

## 9. Checklist

- [ ] `config/cities/<city>.yaml` exists and has a `country`.
- [ ] City key is unique repo-wide; names make sense for `<city>_<country>`.
- [ ] Either queries added to `CITY_QUERIES` **or** a hand-written AOI file is
      in place.
- [ ] `make aois` ran and the boundary opens correctly in a GIS viewer.
- [ ] (finetune/train) reference polygons follow
      `<city>_<country>_reference_<year>.geojson` in `reference_data/`.
- [ ] `make city CITY=<city>` completes and
      `outputs/<city>_<year>_sdg_stats.json` exists.
- [ ] `make report` includes the new city sheet.