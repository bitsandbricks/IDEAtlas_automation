# test_fixtures/ — Sample data for the automated tests

This folder documents and stores (as small sample copies) the inputs used by
the automated end-to-end tests. The actual working data lives under
`ai-dua-mapping/data/raw/`; the samples here are only snapshots so the test
inputs are understandable and reviewable.

## How the fixtures are produced

`tools/fetch_test_fixtures.py` generates everything by calling the **real**
ai-dua-mapping download functions on a small real area (a ~6.6 x 6.6 km
square near Coronel Oviedo, Paraguay — the default example area, changeable
with `--bbox`). Run it once inside the `ideatlas`
environment:

    conda activate ideatlas
    make fixtures

It creates, under `ai-dua-mapping/data/raw/`:

- `aoi/testcity_testland_aoi.geojson`, `aoi/testcity_ref_testland_aoi.geojson`
  — square Area Of Interest for the two fictional cities;
- `sentinel/testcity_testland/S2_2025.tif` (and a copy for `testcity-ref`) —
  Sentinel-2 imagery;
- `buildings/testcity_testland_bldg.gpkg` (and copy) — building footprints;
- `buildings/density/testcity_testland_bd.tif` (and copy) — building density;
- `ghsl/built/TES_GHS_BUILT_*.tif`, `ghsl/pop/TES_GHS_POP_*.tif` — GHSL
  layers, shared by both test cities (the `TES` prefix comes from the first
  three letters of `testcity`);
- `reference_data/testcity_ref_testland_reference_2025.geojson` — a synthetic
  "deprived urban area" reference polygon used only by `testcity-ref` for
  fine-tuning.

## Sample copies stored here

| File | What it is |
|---|---|
| `aoi_sample.geojson` | The square AOI used for the test cities |
| `s2_tile_sample.tif` | The downloaded Sentinel-2 tile |
| `footprints_sample.gpkg` | Building footprints (Google Open Buildings) |
| `ghsl_sample.tif` | GHSL built-up layer |
| `reference_sample_testcity-ref.geojson` | Synthetic reference (DUA) polygon |

## Expected statistics

`expected_sdg_stats_testcity.json` is **not** committed. After the first
successful pipeline run on `testcity`, freeze it so the end-to-end test has
something to compare against:

    cp outputs/testcity_2025_sdg_stats.json test_fixtures/expected_sdg_stats_testcity.json

The end-to-end test only runs when this file exists (otherwise it fails with
instructions).

## Disk footprint

The fixtures themselves (Sentinel-2 tile, GHSL rasters, building footprints)
live under `ai-dua-mapping/data/`, which is git-ignored. Only the small sample
copies above belong to this folder.