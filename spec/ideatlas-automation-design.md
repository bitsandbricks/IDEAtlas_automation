# Diseño de automatización del pipeline IDEAtlas — Asunción, Encarnación, Ciudad del Este

Documento de diseño para implementación con coding agent. Revisado de punta a punta contra el código real de `ai-dua-mapping` (repo de IDEAtlas, https://github.com/IDEAtlas/ai-dua-mapping). Cubre orquestación, componentes propios, manejo de errores, logging, configuración y estructura de repositorio.

---

## 1. Alcance

Automatizar end-to-end el pipeline de IDEAtlas para cualquier conjunto de ciudades (primera implementación: tres ciudades de Paraguay, usadas como ejemplos), **reutilizando `ai-dua-mapping` como framework** (no reimplementando sus pasos internos), y agregando la capa de automatización que ese repo no provee: orquestación multi-ciudad, fallback automático de task, config por ciudad, logging estructurado, reintentos, y reporte consolidado.

## 2. Principios de diseño

- **Reusar el framework real, no reimplementarlo.** `ai-dua-mapping` (`main.py`, `preprocessing/prepare_data.py`, `utils/pipelines.py`) ya implementa descarga de datos, preprocesamiento, entrenamiento/fine-tuning/clasificación y estadísticas SDG, con su propia idempotencia interna (chequeo de existencia de archivo por paso). Nuestra capa lo trata como **caja semi-opaca por ciudad**: se invoca `python main.py --task ... --city ... --country ... --year ...` como una unidad, no se testean sus pasos internos por separado.
- **Simplicidad sobre potencia**: sin Airflow/Prefect/Luigi. Make + un puñado de wrappers propios.
- **Fail-fast en errores permanentes, retry en errores transitorios** — aplicado a nivel de la invocación completa de `main.py` (ver §6), ya que no tenemos visibilidad granular de sus fallos internos.
- **`testcity` es una ciudad más**, no un modo especial de código.
- **Cada dependencia externa que main.py descarga (S2, footprints, GHSL) la resuelve el propio framework real** — nuestra capa no duplica esa lógica ni la envuelve; si `sdg_stats.py` descarga su propia copia de GHSL "pop" por separado de la que `prepare_data.py` baja como "built", se deja así, tal como hace el código real (decisión explícita: no unificar).

---

## 3. Orquestación: Make + wrappers delgados sobre `main.py`

**Lo que YA resuelve `ai-dua-mapping` y por lo tanto no reconstruimos:**
- AOI, Sentinel-2, GHSL built-up, reference raster (con versionado `_v1`/`_v2`), building footprints, PBD, sampling grid, extracción de patches — todo dentro de `preprocessing/prepare_data.py`, con idempotencia por archivo en cada paso.
- Train / finetune / classify — `utils.pipelines.Pipeline`, invocado vía `main.py --task {train,finetune,classify}`.
- Determinismo — `utils/configs.py` fija semillas globales (`random`, `numpy`, `tensorflow`) desde `cfg.SEED` al cargar la config. No hay que construir esto.

**Lo que agrega nuestra capa (los únicos componentes propios):**

1. **`resolve_task_mode`** — decide el `--task` efectivo antes de invocar `main.py`. `prepare_data.py` no tiene fallback: si falta reference data y se pide `train`/`finetune`, hace `sys.exit(1)`. Este wrapper evita eso.
2. **`run_main`** — invoca `python main.py --task <efectivo> --city --country --year`, con:
   - redirección de stdout/stderr a `log/{city}_{year}_main.log` (main.py no escribe a archivo por sí solo, solo a consola vía `logging.basicConfig`)
   - retry a nivel de proceso completo (ver §6)
   - marcador `.done` para idempotencia a nivel Make
3. **`sdg_stats_wrapper`** — llama `utils.sdg_stats.compute_sdg111_stats` **como función importada**, no por subprocess (a diferencia de como lo hace `Pipeline.run_sdg_stats`), para capturar el `summary` que la función ya devuelve y construir un JSON propio con `task_requested`/`task_effective`/`fallback_reason` — el código real solo imprime a stdout y opcionalmente guarda un GPKG/GeoJSON de polígonos, no un JSON de resumen.
4. **`generate_report`** — consolida los 3 `sdg_stats.json` para MUVH/MOPC/INE. No existe en el repo real.

**Makefile resultante:**

```makefile
CITY ?=
COUNTRY ?=
YEAR ?=
TASK ?= classify

TASK_MODE_FILE = data/processed/$(CITY)_$(YEAR)_task_mode.json
MAIN_DONE_MARKER = data/processed/$(CITY)_$(YEAR)/.main_done
SDG_STATS = outputs/$(CITY)_$(YEAR)_sdg_stats.json

$(TASK_MODE_FILE):
	python -m pipeline.resolve_task_mode --city $(CITY) --country $(COUNTRY) --year $(YEAR) --task $(TASK) --out $@

$(MAIN_DONE_MARKER): $(TASK_MODE_FILE)
	python -m pipeline.run_main --city $(CITY) --country $(COUNTRY) --year $(YEAR) --task-mode $(TASK_MODE_FILE)
	touch $@

$(SDG_STATS): $(MAIN_DONE_MARKER) $(TASK_MODE_FILE)
	python -m pipeline.sdg_stats_wrapper --city $(CITY) --country $(COUNTRY) --year $(YEAR) --task-mode $(TASK_MODE_FILE) --out $@

.PHONY: city
city: $(SDG_STATS)

CITIES = asuncion:paraguay encarnacion:paraguay ciudad-del-este:paraguay

.PHONY: all-cities
all-cities:
	@for pair in $(CITIES); do \
		city=$${pair%%:*}; country=$${pair##*:}; \
		$(MAKE) city CITY=$$city COUNTRY=$$country YEAR=$(YEAR) TASK=$(TASK) & \
	done; wait
```

**Testeo**: `resolve_task_mode` y `sdg_stats_wrapper` se testean unitario, con fixtures, sin red. `run_main` se testea a **nivel de integración** con `testcity` (`make city CITY=testcity ...` completo) — no hay testeo aislado de sub-pasos internos de `main.py`, porque esos sub-pasos son del framework real, no nuestros.

---

## 4. Componentes propios 

| Componente | Qué hace | Input | Output | Testeo |
|---|---|---|---|---|
| `resolve_task_mode` | Decide task efectivo según presencia de reference data | task pedido, ciudad, año | `task_mode.json` | Unitario: con/sin reference, 3 casos como antes |
| `run_main` | Invoca `main.py` real, redirige logs, retry, marcador de idempotencia | `task_mode.json` | `.main_done` + `log/{city}_{year}_main.log` | Integración con `testcity`/`testcity-ref` |
| `sdg_stats_wrapper` | Llama `compute_sdg111_stats` como función, arma JSON con metadata de fallback | mapa DUA + población (ya generados por `main.py`) + `task_mode.json` | `sdg_stats.json` | Unitario con fixtures + valores calculados a mano |
| `generate_report` | Consolida 3 `sdg_stats.json` | 3× `sdg_stats.json` | reporte MUVH/MOPC/INE | Pendiente de formato |

### Fallback automático (`resolve_task_mode`) — sin cambios de diseño
```json
{
  "task_requested": "finetune",
  "task_effective": "classify",
  "fallback_reason": "reference_data_not_found"
}
```

---

## 5. Config por ciudad


```
config/
  base.yaml                  # incluye SEED: 42 explícito (antes dependía del default de utils/configs.py)
  cities/
    asuncion.yaml
    encarnacion.yaml
    ciudad-del-este.yaml
    testcity.yaml
    testcity-ref.yaml
```

- `SEED` se agrega **explícito** en `base.yaml` (aunque `utils/configs.py` ya usa 42 como default vía `getattr`) — para que quede documentado y no dependa de un default silencioso si el repo de IDEAtlas cambia ese default en el futuro.
- El resto de la convención (merge `base` + `cities/{city}.yaml`, `COUNTRY`/AOI en YAML, `YEAR`/`TASK` como argumentos) se mantiene igual.
- **Importante**: `config.yaml` de `ai-dua-mapping` (hiperparámetros del modelo: `N_EPOCHS`, `LR`, `PATIENCE`, etc.) es un archivo **distinto** al `config/base.yaml` de nuestra capa de automatización — no los mezclamos. El de `ai-dua-mapping` se deja tal cual provisto; el nuestro es solo para orquestación (ciudad, país, año, seed, paths de logging).

---

## 6. Manejo de errores y reintentos — revisado

Como ya no tenemos wrappers propios alrededor de cada descarga individual (S2, footprints, GHSL), el retry ya no se aplica a 4 clientes delgados nuestros — se aplica a **la invocación completa de `main.py`** dentro de `run_main`:

- No podemos distinguir con precisión error transitorio vs. permanente sin parsear el log de `main.py` (no nos da un código de error estructurado, solo exit code + texto). Política simplificada: **reintentar la llamada completa hasta N veces** (ej. 2) con backoff, salvo que el log contenga patrones claramente permanentes (`"No reference data file found"`, `"AOI file not found"`) — en cuyo caso no reintentar.
- Esto es barato gracias a la idempotencia interna de `prepare_data.py`: un reintento después de un fallo transitorio de red retoma en el paso que falló, no vuelve a descargar lo ya bajado.
- `resolve_task_mode` y `sdg_stats_wrapper` sí son nuestros de punta a punta — mantienen `TransientError`/`PermanentError` y `with_retry` tal como se diseñó, para sus propias dependencias (lectura/escritura de archivos, en general sin red).
- Exit code ≠ 0 de `run_main` → Make detiene esa rama
- Escritura atómica aplica a `task_mode.json` y `sdg_stats.json` (nuestros outputs); los outputs internos de `main.py` (pesos, rasters) quedan bajo su propia responsabilidad de escritura.

---

## 7. Fixtures de test y clientes de red — generación, no producción

El único lugar donde tocamos las funciones de descarga de `ideatlas_core` directamente es **para generar las fixtures de test, una sola vez, a mano** — como ya se hizo (`s2_tile_sample.tif`, `footprints_sample.gpkg`, `ghsl_sample.tif` generados con `stac_api.get_s2`, `google_buildings.download_google_open_buildings`, `fetch_ghsl.GHSLDownloader`).

Ese script de generación de fixtures vive en `tools/` o `scripts/`, **no en `pipeline/`** — es tooling de desarrollo, no parte del pipeline de automatización que corre en producción.

Tests de integración real (llamando APIs externas de verdad) ya no aplican a nuestra capa — si algo cambia en las APIs que usa `ai-dua-mapping`, se detecta corriendo `main.py` directamente, es responsabilidad de ese repo, no de nuestra automatización.

---

## 8. Ciudad de prueba (`testcity`)

- `CITY=testcity COUNTRY=testland YEAR=2025`, igual que antes.
- Fixtures ya generadas con las funciones reales, colocadas en las rutas que `prepare_data.py` espera (`data/raw/aoi/testcity_testland_aoi.geojson`, etc.) — mismo naming real (`city_normalized = f"{city}_{country}"`, confirmado en `main.py`).
- `testcity` (sin reference data) y `testcity-ref` (con reference data), mismo propósito que antes.
- **`SEED: 42` explícito** en `config/cities/testcity.yaml` y `testcity-ref.yaml` (o en `base.yaml`, heredado).
- El test de `testcity`/`testcity-ref` ahora es de **integración**, no granular: `make city CITY=testcity-ref ... TASK=finetune` corre `main.py` real de punta a punta y se compara `sdg_stats.json` contra `expected_sdg_stats_testcity.json`. Ya no hay tests aislados de `align_and_clip`/`make_patches`/`split_dataset` porque esos pasos no son nuestros.

---

## 9. Logging

- **Nuestros wrappers** (`resolve_task_mode`, `sdg_stats_wrapper`, `generate_report`) usan `get_logger(component, city, year)` como se diseñó — JSON estructurado, archivo + stderr.
- **`main.py`** no escribe a archivo por sí solo (solo `logging.basicConfig` a consola). `run_main` es responsable de **redirigir su stdout/stderr** a `log/{city}_{year}_main.log` al invocarlo como subprocess — esto cubre también las barras de progreso (`tqdm`) y los logs internos de `prepare_data.py`.
- El warning de fallback sigue logueándose en el momento por `resolve_task_mode`, antes de que `run_main` siquiera se invoque.
- Resto de la sección (formato, niveles, `tmp_path` en tests) sin cambios.

---

## 10. Estructura de carpetas del repo — simplificada

```
ideatlas-automation/
├── Makefile
├── config/
│   ├── base.yaml                 # incluye SEED explícito
│   └── cities/
│       ├── asuncion.yaml
│       ├── encarnacion.yaml
│       ├── ciudad-del-este.yaml
│       ├── testcity.yaml
│       └── testcity-ref.yaml
│
├── pipeline/
│   ├── __init__.py
│   ├── config.py                 # load_config(city)
│   ├── logging_utils.py          # get_logger(component, city, year)
│   ├── retry.py                  # with_retry, TransientError, PermanentError (usado por resolve_task_mode, sdg_stats_wrapper, run_main)
│   ├── resolve_task_mode.py
│   ├── run_main.py                # invoca main.py real, redirige logs, retry a nivel proceso
│   ├── sdg_stats_wrapper.py       # llama utils.sdg_stats.compute_sdg111_stats como función
│   └── generate_report.py
│
├── ai-dua-mapping/                 # ai-dua-mapping, clonado de su repo, sin modificar
│                                   # (main.py, preprocessing/, utils/, models/, config.yaml, checkpoint/)
│
├── tools/
│   └── fetch_test_fixtures.py     # genera fixtures a mano usando las funciones reales de ideatlas_core (uso único, no producción)
│
├── tests/
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_retry.py
│   │   ├── test_resolve_task_mode.py
│   │   └── test_sdg_stats_wrapper.py
│   └── e2e/
│       └── test_testcity_pipeline.py   # make city CITY=testcity(-ref) end-to-end
│
├── test_fixtures/
│   ├── aoi_sample.geojson
│   ├── s2_tile_sample.tif
│   ├── footprints_sample.gpkg
│   ├── ghsl_sample.tif
│   ├── reference_sample_testcity-ref.geojson
│   └── expected_sdg_stats_testcity.json
│
├── data/                           # generado por ideatlas_core en runtime, no versionado
├── log/                            # no versionado
├── outputs/                        # nuestros sdg_stats.json (versionado opcional)
│
├── .gitignore
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 11. Pendiente para la fase de implementación

- Confirmar el patrón exacto de detección "error permanente" en el log de `main.py` para la política de retry de §6 (los mensajes de error son texto libre, no códigos — revisar si vale la pena, o si conviene simplemente reintentar siempre hasta N veces sin distinguir).
- Definir cómo `run_main` invoca `main.py`: `subprocess.run` directo, o import + llamada a `main()` en proceso (subprocess es más aislado y más fácil de loguear con redirección; se recomienda subprocess salvo que el overhead de arrancar TensorFlow por ciudad sea un problema real de performance en `all-cities`).
- Verificar si `main.py` acepta correr con GPU compartida entre las 3 ciudades en paralelo (`all-cities`) sin conflictos — si el entrenamiento usa GPU, correr 3 en paralelo puede no ser viable y `all-cities` debería serializar el paso de `run_main` aunque el resto quede paralelo.
- Diseño de `generate_report` (formato: markdown, docx, xlsx) — sigue sin definir.
- Decidir si `outputs/*.json` se versiona en git.
