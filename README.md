# IDEAtlas automation layer

Automated, reproducible pipeline for measuring the **proportion of the urban
population living in deprived urban areas** (SDG indicator **11.1.1**) with
the [IDEAtlas](https://github.com/IDEAtlas) methodology, in any region. It
ships with three **example cities** (from its first implementation in
Paraguay, kept as a reference) and supports additional cities — just drop a
YAML file into `config/cities/`:

- **Asunción** (`asuncion`)
- **Encarnación** (`encarnacion`)
- **Ciudad del Este** (`ciudad-del-este`)

This repository does **not** re-implement the mapping model. It wraps the
real IDEAtlas framework (`ai-dua-mapping`, cloned into `ai-dua-mapping/` and
left untouched) with a thin automation layer that:

1. **Decides** which task can actually run (falls back to `classify` when a
   requested `finetune`/`train` has no reference data);
2. **Runs** the framework's `main.py` for every step, retrying transient
   failures and stopping immediately on permanent ones;
3. **Computes** the SDG 11.1.1 statistics as a clean JSON document;
4. **Reports** everything in a single Markdown + Excel report.

The AI-model piece is the pre-trained global MBCNN model shipped inside the
framework, optionally fine-tuned with your own reference data.

> Intended audience: GIS / territorial-studies practitioners. You do **not**
> need to be a developer to follow this guide — commands are run with
> `conda` and `make`.

---

## Quick start

```bash
# 1. One-time setup: create the 'ideatlas' conda environment (TensorFlow,
#    geopandas, ...) and install the small extra packages.
make setup
conda activate ideatlas

# 2. One-time: fetch city boundaries (AOIs) from OpenStreetMap.
make aois

# 3. Run a single city end to end (Sentinel-2 download -> classification ->
#    population census -> SDG 11.1.1 statistics).
make city CITY=asuncion

# 4. Run all three cities (serial by default), then build the report.
make all-cities
make report
```

Your results land in `outputs/`:

```
outputs/
  ideatlas_report.md          <- readable summary (open in any text editor)
  ideatlas_report.xlsx        <- spreadsheet version (Excel / QGIS)
  asuncion_2025_sdg_stats.json
  encarnacion_2025_sdg_stats.json
  ciudad-del-este_2025_sdg_stats.json
```

The classified maps themselves are produced by the framework inside
`ai-dua-mapping/output/` (e.g.
`asuncion_paraguay.s2.bd.mbcnn.2025.tif`) — open them in QGIS alongside the
report.

Every command prints to the console **and** writes structured JSON logs in
`log/` for later analysis (who ran what, when, and what happened).

---

## What the pipeline does, step by step

For each city the pipeline runs three small programs (`pipeline/` package);
`make` chains them together automatically:

### 1. Decide the task — `pipeline.resolve_task_mode`

The framework cannot attempt a fine-tuning run without *reference data*
(manually mapped informal/slum polygons). Instead of letting it crash, we
check for the data first:

- `classify` / `sdg_stats` never need reference data → kept as requested;
- `finetune` / `train` **without** reference data → automatically fall back
  to `classify` with the pre-trained global model, and the reason is recorded.

The decision is written to `data/processed/<city>_<year>_task_mode.json`.

### 2. Run the model — `pipeline.run_main`

Calls the framework's `main.py` with the effective task:

- `classify` → download Sentinel-2 for the city AOI, predict informal vs
  formal built-up areas with the global MBCNN model;
- `finetune`/`train` (only when reference data exists) → additionally trains
  a city model, then **automatically classifies with it**;
- `sdg_stats` → (handled by the next step).

Transient failures (dropped downloads, timeouts) are retried with backoff;
permanent failures (missing data, no internet results) abort immediately with
a clear message. Note that the whole `classify` step is not restartable in
the middle by the framework — the tool re-runs it from scratch on retry.

### 3. Compute the statistics — `pipeline.sdg_stats_wrapper`

Imports the framework's own statistics function and computes, per city:

- **DUA** — Deprived (informal) urban areas
- **NDUA** — Non-deprived (formal) urban areas

…as area, population and percentage, using the built-up classification and
the GHSL population grid. The numeric summary is persisted to
`outputs/<city>_<year>_sdg_stats.json` **as a computer-readable file**
(no console output parsing).

### 4. Report — `pipeline.generate_report`

`make report` reads all `outputs/*_sdg_stats.json` documents and writes
`ideatlas_report.md` and `ideatlas_report.xlsx`.

---

## Project structure

```
config/
  base.yaml                    # global defaults (framework dir, retry policy, ...)
  cities/<city>.yaml           # per-city config (country, year)
pipeline/                      # the automation layer
  config.py                    # loads & merges configs, derives framework paths
  logging_utils.py             # JSON logs in log/
  retry.py                     # permanent-error blacklist + retry/backoff
  resolve_task_mode.py         # step 1
  run_main.py                  # step 2
  sdg_stats_wrapper.py         # step 3
  generate_report.py           # step 4
tools/
  fetch_city_aois.py           # one-time: AOI download (Nominatim)
  fetch_test_fixtures.py       # dev/test only: synthetic test-city data
tests/
  unit/                        # offline unit tests (no framework needed)
  e2e/                         # end-to-end test on the test cities
test_fixtures/                 # sample data for the tests (see its README)
ai-dua-mapping/                # the IDEAtlas framework (cloned, untouched)
outputs/                       # reports + statistics (git-ignored)
log/                           # JSON logs (git-ignored)
Makefile                       # the commands listed below
```

---

## Reference: `make` targets

| Target | What it does |
|---|---|
| `make setup` | Create/update the `ideatlas` conda environment (run once). |
| `make aois` | Fetch the three city boundaries from OSM/Nominatim. |
| `make city CITY=<city>` | Full single-city run (task decision → framework → SDG stats). |
| `make all-cities` | Run `asuncion`, `encarnacion`, `ciudad-del-este` (serial). |
| `make all-cities PARALLEL=1` | Same, but the three cities run concurrently. |
| `make report` | Build the consolidated Markdown + Excel report. |
| `make test` | Offline unit tests. |
| `make e2e` | End-to-end test on synthetic test cities (needs fixtures). |
| `make help` | Show this overview. |

`make` variables: `CITY`, `YEAR` (default `2025`), `TASK` (default
`classify`; also `finetune`/`train`), `WEIGHTS` (optional weights file for
the classification step), `PARALLEL` (`1` for parallel cities).

Example: force a fine-tune for Asunción and classify with the resulting city
model:

```bash
make city CITY=asuncion TASK=finetune
```

If Asunción has no reference data yet, the pipeline will silently (well —
loudly, in the log) fall back to `classify`. If you later add reference data
(see below), the same command trains and uses a city-specific model.

---

## Reference data (recommended for better accuracy)

The pre-trained global model already gives a first classification. To fine-tune
a city model you need a **reference dataset**: vector polygons marking known
**deprived urban areas** (DUA) in that city.

Drop the file(s) into:

```
ai-dua-mapping/data/raw/reference_data/
```

Naming follows the framework's expectations:

```
<city>_<country>_reference_<year>.geojson
# e.g. asuncion_paraguay_reference_2025.geojson
```

…or with a version suffix (`..._reference_2025_v2.geojson`). Both `.geojson`
and raster `.tif` reference files are supported.

Then run:

```bash
make city CITY=asuncion TASK=finetune
```

The pipeline detects the reference data, trains a city model, saves it to
`ai-dua-mapping/checkpoint/asuncion_paraguay.s2.bd.mbcnn.weights.h5` and
classifies with it automatically.

---

## The two functions per city AOI

The AOI (Area Of Interest) is the city boundary used to download imagery. It
is fetched once from OpenStreetMap (`make aois`) into
`ai-dua-mapping/data/raw/aoi/<city>_<country>_aoi.geojson`. If you prefer a
custom polygon (e.g. an official municipal boundary), replace that file with
your own GeoJSON in WGS84 (EPSG:4326) — the pipeline will use it as-is.

---

## Caveats and interpretation

- **Downloaded inputs and outputs live under `ai-dua-mapping/`** (its own
  `data/`, `output/`, `checkpoint/`, `log/` folders). This is intentional:
  the framework only understands paths relative to its own directory (its
  `config.yaml` uses `./data/raw/`, `./output/`, ...) and we keep the clone
  unmodified and pinned. Our reproducible results are separate (`outputs/`,
  `log/`); everything under `ai-dua-mapping/` is git-ignored.
- **The `classify` step downloads Sentinel-2 imagery**: it needs internet and
  takes a while on the first run (hundreds of MB).
- **GHSL population models tend to undercount people in deprived areas**;
  treat absolute population figures as estimates, the percentages are the
  SDG 11.1.1 headline number.
- **`sdg_stats` requires the classified map**: `make report` warns if a city
  has no statistics yet.
- A `rasterio/warp.py:344 NotGeoreferencedWarning: ... identity matrix`
  message during the Sentinel-2 download is **expected and harmless**: it is
  raised against an intermediate object inside the STAC reproject step. The
  written tile is verified EPSG:4326 and correctly aligned to the AOI (you
  can check it in QGIS).
- The framework step output is streamed to `log/<city>_<year>_<task>.log`
  and the JSON summaries are written into `log/<city>_<year>_<component>.jsonl`.
- Parallel runs (`PARALLEL=1`) share the framework's data/checkpoint folders:
  fine, unless two cities try the same task at the same time with identical
  names (our test cities share the `TES` GHSL prefix by design).

---

## Tests

The automated tests use two **fictional** cities (`testcity`,
`testcity-ref`) so the real project data is never polluted:

- `make test` — offline unit tests of the automation layer (fast, no AI
  framework needed);
- `make e2e` — runs the full pipeline on the test city and compares the
  numbers against a frozen expectation (needs the `ideatlas` environment and
  the fixtures generated by `make fixtures`).

See `test_fixtures/README.md` for how the fixtures are produced.

---

## Notes for maintainers

- Configuration values that may change between runs (city, year, task) live
  in `config/cities/<city>.yaml` and in the `make` variables, never in code.
- The framework is pulled at a **pinned commit** (see
  `ai-dua-mapping/.git`) so reproductions are deterministic; update the
  pinned commit intentionally.
- All persistent outputs and logs are git-ignored by design (`.gitignore`).
- The permanent-error list in `pipeline/retry.py` mirrors the framework's
  current error wording; if the framework changes its messages, update that
  tuple.