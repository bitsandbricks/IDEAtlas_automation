# IDEAtlas Colab notebook

`IDEAtlas_pipeline.ipynb` runs the full IDEAtlas SDG 11.1.1 automation
(Asunción / Encarnación / Ciudad del Este, or any custom area) entirely on
Google Colab — **no local GPU, no Python setup**. Results are copied to your
Google Drive.

## Quickstart

1. Make sure your copy of this repository is **public on GitHub**, then open
   the notebook in Colab:

   ```
   https://colab.research.google.com/github/<your-user>/IDEAtlas_automation/blob/main/notebooks/IDEAtlas_pipeline.ipynb
   ```

   (Alternatively: `File ▸ Upload notebook…`.)

2. Pick a **T4 GPU** runtime: `Runtime ▸ Change runtime type ▸ T4 GPU`.

3. Edit the **Settings** cell (the `REPO_URL` line and `CITY`), then
   `Runtime ▸ Run all`.

4. Click **Allow** when Google asks to mount your Drive.

First run ≈10 min (environment setup), then 20–40 min per city.

## What you get

Results are written to `MyDrive/IDEAtlas/<city>/`:

- `SUMMARY/` — the SDG 11.1.1 statistics (`<city>_<year>_sdg_stats.json`) and
  the human-readable report (`ideatlas_report.md` + `.xlsx`).
- `FULL/` — everything the pipeline produced (classified map `.tif`, masks,
  processed layers, AOI, logs). Can be hundreds of MB.

## Settings cell

| Setting | Meaning |
| --- | --- |
| `REPO_URL` | GitHub URL of *this* repo. Must be public (or a token URL, below). |
| `CITY` | `"encarnacion"`, `"asuncion"`, `"ciudad-del-este"` — or `"custom"`. |
| `YEAR` | Processing year (2025 unless your `config/cities/<city>.yaml` differs). |
| `NEW_CITY` / `NEW_COUNTRY` / `NEW_AOI_DRIVE` | Used only when `CITY = "custom"`. |

## Custom area (instead of a city)

The notebook runs any custom polygon you provide:

1. Prepare a boundary file in WGS84: a GeoJSON `FeatureCollection` with one
   `Polygon`/`MultiPolygon` feature (see `docs/adding-a-city.md`, Option B).
   Draw it in QGIS / geojson.io.
2. Upload it to Google Drive.
3. Set `CITY = "custom"`, `NEW_CITY` (e.g. `my-town`), `NEW_COUNTRY`, and
   `NEW_AOI_DRIVE` to the file's full Drive path, then *Run all*.

The config file, AOI placement and naming are handled automatically.

## Notes & troubleshooting

- **The repo must be public** for `git clone` to work without credentials.
  For a private repo, use a fine-grained personal-access-token URL in
  `REPO_URL`, e.g. `https://<token>@github.com/<user>/IDEAtlas_automation`
  (do not leave the notebook with a live token).
- **Restart after the conda install:** installing Miniconda restarts the
  Colab runtime once, but the clone, Miniconda and the `ideatlas` env survive
  a *kernel* restart — re-run the next cell (Step 4) and then continue from
  Step 6. Only a full **Factory reset runtime** wipes `/content`; re-run the
  notebook from the top in that case.
- **Session limits:** free Colab disconnects after ~90 min idle / ~12 h total,
  releasing the VM and wiping `/content`. If your run is interrupted there,
  re-run the notebook from the top — everything rebuilds (AOIs already fetched
  are skipped, `make city` resumes from its markers) and results already
  copied to Drive are safe.
- **Slow / no GPU:** always export the network, then choose the T4 GPU at the
  top. The notebook prints the detected GPU in Step 2 and a TensorFlow GPU
  check after the environment is built.
- **AOI looks wrong:** check `FULL/` (or
  `ai-dua-mapping/data/raw/aoi/`) — for big cities a *bounding-box fallback*
  rectangle may have been used (visible in the output log); provide a hand-made
  AOI as a custom city to get the exact municipal boundary.
- This notebook installs the exact tested environment
  (`ai-dua-mapping/environment.yaml`, Python 3.10) inside a Miniconda env
  called `ideatlas`; it does not touch your Colab account or the framework code.

## Full pipeline details

See the top-level `README.md`, `docs/adding-a-city.md`, and the
`Makefile` for how the same work runs locally with `make all-cities`.