# ============================================================================
# IDEAtlas automation layer - Make targets
# ============================================================================
# The Makefile orchestrates the real ai-dua-mapping framework for each city.
# The supported cities live in config/cities/. Command-line variables:
#
#   CITY      city name (asuncion, encarnacion, ciudad-del-este, testcity, ...)
#   YEAR      year to process (default 2025)
#   TASK      task to run (default classify; also finetune / train)
#   WEIGHTS   optional weights file passed to classify/finetune
#   PARALLEL  1 to process all cities in parallel (default 0 = serial)
#
# Typical usage after `conda activate ideatlas`:
#
#   make city CITY=asuncion                 # one city, default classify
#   make all-cities YEAR=2025               # the example cities
#   make report                             # consolidated markdown + xlsx
#   make help                               # this overview
# ============================================================================

CITY ?=
YEAR ?= 2025
TASK ?= classify
WEIGHTS ?=
PARALLEL ?= 0
SOURCE ?= nominatim
FUA_DATA ?=

# Python interpreter from the active (ideatlas) conda environment.
PYTHON ?= python

# Conda environments no longer export $CONDA_PREFIX/lib to LD_LIBRARY_PATH by
# default, yet the cudatoolkit/cudnn packages live there and TensorFlow needs
# them at load time to use the GPU (otherwise it silently falls back to CPU).
# Inject the interpreter's own lib dir on every target, so GPU inference works
# from any shell/activation. A stale entry pointing at a removed /usr/local/cuda
# (leftover in .bashrc) is harmless once this env lib comes first.
PYTHON_LIBS := $(shell $(PYTHON) -c 'import sys,os; print(os.path.join(sys.prefix,"lib"))' 2>/dev/null)
PYTHON := env LD_LIBRARY_PATH="$(PYTHON_LIBS):$$LD_LIBRARY_PATH" $(PYTHON)

# --- File locations used by the pipeline steps --------------------------------
# Task decision (which task actually runs after any fallback):
TASK_MODE_FILE = data/processed/$(CITY)_$(YEAR)_task_mode.json
# Marker created when main.py (and any chained classify) finished successfully:
MAIN_DONE_MARKER = data/processed/$(CITY)_$(YEAR)/.main_done
# Per-city SDG 11.1.1 statistics document:
SDG_STATS_FILE = outputs/$(CITY)_$(YEAR)_sdg_stats.json

# Default example cities from the first implementation (Paraguay), kept as a
# reference: replace with "<city>:<country>" pairs for your own region.
CITIES = asuncion:paraguay encarnacion:paraguay ciudad-del-este:paraguay

.PHONY: help

help:
	@echo ""
	@echo "IDEAtlas automation - available targets"
	@echo "========================================="
	@echo ""
	@echo "  make setup        Create the 'ideatlas' conda environment and install"
	@echo "                    the thin-layer dependencies (run once)."
	@echo ""
	@echo "  make aois [SOURCE=fua] [FUA_DATA=<gpkg>]  Fetch city boundaries (AOIs) for the cities in"
	@echo "                    config/cities/. Defaults to OpenStreetMap/Nominatim;"
	@echo "                    SOURCE=fua reads the GHS Functional Urban Areas"
	@echo "                    GeoPackage instead (ai-dua-mapping/data/raw/ghsl/fua/"
	@echo "                    or the FUA_DATA path)."
	@echo ""
	@echo "  make prob CITY=...  Re-run inference and persist the continuous"
	@echo "                    informal/DUA probability raster (float32) next to"
	@echo "                    the classification map, for evaluation."
	@echo ""
	@echo "  make fixtures     Generate the synthetic test-city (testcity) input data"
	@echo "                    using the real framework download functions."
	@echo ""
	@echo "  make city CITY=<city> [YEAR=...] [TASK=...] [WEIGHTS=...]"
	@echo "        Runs one city end to end:"
	@echo "          1. decide the effective task (falls back to 'classify' when the"
	@echo "             requested 'finetune'/'train' has no reference data),"
	@echo "          2. run the IDEAtlas framework (with an automatic classify step"
	@echo "             after finetune/train),"
	@echo "          3. compute the SDG 11.1.1 statistics (sdg_stats.json)."
	@echo ""
	@echo "  make all-cities [YEAR=...] [TASK=...] [PARALLEL=1]"
	@echo "        Runs 'city' for every country:city pair defined in the Makefile."
	@echo "        PARALLEL=1 processes them at the same time (default: serial)."
	@echo ""
	@echo "  make report      Consolidate all sdg_stats.json into ideatlas_report.md"
	@echo "                   and ideatlas_report.xlsx in the outputs/ folder."
	@echo ""
	@echo "  make test        Run the offline unit tests."
	@echo "  make e2e         Run the end-to-end test on the testcity fixtures."
	@echo ""
	@echo "Variables: CITY, YEAR (2025), TASK (classify), WEIGHTS, PARALLEL (0),"
	@echo "           SOURCE (nominatim), FUA_DATA (epoch 2015 GeoPackage)."

# --- One city, end to end ------------------------------------------------------

$(TASK_MODE_FILE):
	$(PYTHON) -m pipeline.resolve_task_mode --city $(CITY) --year $(YEAR) --task $(TASK) --out $@

$(MAIN_DONE_MARKER): $(TASK_MODE_FILE)
	$(PYTHON) -m pipeline.run_main --city $(CITY) --year $(YEAR) --task-mode $(TASK_MODE_FILE) $(if $(WEIGHTS),--weights $(WEIGHTS),)

$(SDG_STATS_FILE): $(MAIN_DONE_MARKER)
	$(PYTHON) -m pipeline.sdg_stats_wrapper --city $(CITY) --year $(YEAR) --task-mode $(TASK_MODE_FILE) --out $@

.PHONY: city
city: $(SDG_STATS_FILE)

# --- All example cities -------------------------------------------------------

.PHONY: all-cities all-cities-serial all-cities-parallel

all-cities-serial:
	@set -e; for pair in $(CITIES); do \
		city=$${pair%%:*}; country=$${pair##*:}; \
		echo ""; echo "=== City: $$city ($$country) ==="; \
		$(MAKE) city CITY=$$city YEAR=$(YEAR) TASK=$(TASK) $(if $(WEIGHTS),WEIGHTS=$(WEIGHTS),) || exit 1; \
	done

all-cities-parallel:
	@set -e; pids=""; \
	for pair in $(CITIES); do \
		city=$${pair%%:*}; country=$${pair##*:}; \
		echo ""; echo "=== City: $$city ($$country) ==="; \
		$(MAKE) city CITY=$$city YEAR=$(YEAR) TASK=$(TASK) $(if $(WEIGHTS),WEIGHTS=$(WEIGHTS),) & \
		pids="$$pids $$!"; \
	done; \
	for p in $$pids; do wait $$p || exit 1; done

all-cities: $(if $(filter 1,$(PARALLEL)),all-cities-parallel,all-cities-serial)

# --- Report --------------------------------------------------------------------

.PHONY: report
report:
	$(PYTHON) -m pipeline.generate_report

# --- Environment and development tooling ---------------------------------------

.PHONY: setup aois prob fixtures test e2e

setup:
	@echo "Creating/updating conda environment 'ideatlas' from ai-dua-mapping/environment.yaml ..."
	@conda env create -n ideatlas -f ai-dua-mapping/environment.yaml 2>/dev/null \
		|| conda env update -n ideatlas -f ai-dua-mapping/environment.yaml
	@echo "Installing thin-layer dependencies ..."
	@conda run -n ideatlas pip install pyyaml openpyxl pytest
	@echo "Done. Activate the environment with:  conda activate ideatlas"

aois:
	@mkdir -p ai-dua-mapping/data/raw/aoi
	@$(PYTHON) tools/fetch_city_aois.py --out ai-dua-mapping/data/raw/aoi --source $(SOURCE) $(if $(FUA_DATA),--fua-data $(FUA_DATA),)

# Re-run inference but persist the continuous informal/DUA probability raster
# next to the classification map (ai-dua-mapping/output), for evaluation.
prob:
	$(PYTHON) tools/make_informal_prob.py --city $(CITY) --year $(YEAR) $(if $(WEIGHTS),--weights $(WEIGHTS),)

fixtures:
	@$(PYTHON) tools/fetch_test_fixtures.py

test:
	@$(PYTHON) -m pytest tests/unit

e2e:
	@$(PYTHON) -m pytest tests/e2e
