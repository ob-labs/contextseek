PYTHON ?= python3
UV ?= uv
UV_HTTP_TIMEOUT ?= 300

# Resolve project root regardless of whether this Makefile is invoked from
# the project root (make -f eval/Makefile) or from inside eval/ (make).
PROJECT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

CONTEXTSEEK_PYTHON ?= $(PROJECT_ROOT)/.venv/bin/python
APPWORLD_VENV ?= $(PROJECT_ROOT)/.venv-appworld
APPWORLD_PYTHON ?= $(APPWORLD_VENV)/bin/python
APPWORLD_PIP := $(APPWORLD_PYTHON) -m pip
APPWORLD_ROOT ?= $(PROJECT_ROOT)/.appworld
APPWORLD_CACHE ?= $(PROJECT_ROOT)/.appworld-cache
APPWORLD_ENV := APPWORLD_ROOT="$(APPWORLD_ROOT)" APPWORLD_CACHE="$(APPWORLD_CACHE)"
APPWORLD_RUN := cd "$(PROJECT_ROOT)" && APPWORLD_PYTHON="$(APPWORLD_PYTHON)" $(APPWORLD_ENV) $(CONTEXTSEEK_PYTHON) -m eval.appworld.run
APPWORLD_DATA_MARKER ?= $(APPWORLD_ROOT)/.data-ready
APPWORLD_SKIP_DOWNLOAD ?= 0
APPWORLD_FORCE_NO_RESUME ?= 0
APPWORLD_NO_RESUME_FLAG := $(if $(filter 1 true TRUE yes YES,$(APPWORLD_FORCE_NO_RESUME)),--no-resume,)
TAUBENCH_VENV ?= $(PROJECT_ROOT)/.venv-taubench
TAUBENCH_PYTHON ?= $(TAUBENCH_VENV)/bin/python
TAUBENCH_SOURCE ?= $(PROJECT_ROOT)/.tau2-bench
TAUBENCH_REPO ?= https://github.com/sierra-research/tau2-bench.git
TAUBENCH_ENV := TAU2_DATA_DIR="$(TAUBENCH_SOURCE)/data"
TAUBENCH_RUN := cd "$(PROJECT_ROOT)" && $(TAUBENCH_ENV) $(TAUBENCH_PYTHON) -m eval.taubench.run

.PHONY: help \
	appworld-envs appworld-install appworld-data appworld-redownload-data \
	appworld-envs-oceanbase appworld-check \
	appworld-bench-baseline appworld-bench-file appworld-bench-file-evolve \
	appworld-bench-oceanbase appworld-bench-all appworld-clean-envs \
	taubench-install taubench-install-oceanbase taubench-check \
	taubench-bench-baseline taubench-bench-store taubench-bench-react \
	taubench-bench-evolve taubench-bench-store-oceanbase \
	taubench-bench-react-oceanbase taubench-bench-evolve-oceanbase \
	taubench-bench-oceanbase taubench-bench-all taubench-clean-env \
	taubench-source

help:
	@echo "Usage: make -f eval/Makefile <target>"
	@echo ""
	@echo "AppWorld evaluation:"
	@echo "  appworld-envs              # Create/update .venv and .venv-appworld"
	@echo "  appworld-data              # Install/download AppWorld data once"
	@echo "  appworld-redownload-data   # Force AppWorld data download again"
	@echo "  appworld-check             # Check both benchmark environments"
	@echo "  appworld-bench-baseline    # Run baseline AppWorld benchmark"
	@echo "  appworld-bench-file        # Run file-backend contextseek benchmark"
	@echo "  appworld-bench-file-evolve # Run file-backend benchmark with compact/evolve"
	@echo "  appworld-bench-oceanbase   # Run OceanBase-backed benchmark"
	@echo "  appworld-bench-all         # Run baseline + file + OceanBase benchmarks"
	@echo "  appworld-clean-envs        # Remove local benchmark virtualenvs"
	@echo "  (set APPWORLD_FORCE_NO_RESUME=1 to force rerun all tasks)"
	@echo ""
	@echo "tau-bench evaluation:"
	@echo "  taubench-install           # Create/update isolated .venv-taubench"
	@echo "  taubench-install-oceanbase # Create/update .venv-taubench with OceanBase deps"
	@echo "  taubench-source            # Clone/update local tau2-bench source"
	@echo "  taubench-check             # Check tau-bench imports"
	@echo "  taubench-bench-baseline    # Run baseline tau-bench benchmark"
	@echo "  taubench-bench-store       # Run store-only warm-up + distill"
	@echo "  taubench-bench-react       # Run retrieval-enhanced benchmark"
	@echo "  taubench-bench-evolve      # Run retrieval + evolution benchmark"
	@echo "  taubench-bench-oceanbase   # Run OceanBase store + react + evolve benchmarks"
	@echo "  taubench-bench-all         # Run all tau-bench benchmark configs"
	@echo "  taubench-clean-env         # Remove .venv-taubench"

# ── AppWorld ──────────────────────────────────────────────────────────────────

appworld-envs: appworld-install appworld-data

appworld-install:
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" $(UV) sync --extra appworld-eval
	test -x "$(APPWORLD_PYTHON)" || $(PYTHON) -m venv "$(APPWORLD_VENV)"
	$(APPWORLD_PIP) install --upgrade pip
	$(APPWORLD_PIP) install appworld openai anthropic
	mkdir -p "$(APPWORLD_ROOT)" "$(APPWORLD_CACHE)"
	cd "$(APPWORLD_ROOT)" && $(APPWORLD_ENV) "$(APPWORLD_VENV)/bin/appworld" install

appworld-data: appworld-install
	@if [ "$(APPWORLD_SKIP_DOWNLOAD)" = "1" ]; then \
		echo "APPWORLD_SKIP_DOWNLOAD=1, skip appworld install/download data"; \
	elif [ -f "$(APPWORLD_DATA_MARKER)" ] && [ -d "$(APPWORLD_ROOT)/data/tasks" ]; then \
		echo "AppWorld data marker exists: $(APPWORLD_DATA_MARKER)"; \
	else \
		mkdir -p "$(APPWORLD_ROOT)" "$(APPWORLD_CACHE)"; \
		cd "$(APPWORLD_ROOT)" && $(APPWORLD_ENV) "$(APPWORLD_VENV)/bin/appworld" download data; \
		touch "$(APPWORLD_DATA_MARKER)"; \
	fi

appworld-redownload-data: appworld-install
	rm -f "$(APPWORLD_DATA_MARKER)"
	mkdir -p "$(APPWORLD_ROOT)" "$(APPWORLD_CACHE)"
	cd "$(APPWORLD_ROOT)" && $(APPWORLD_ENV) "$(APPWORLD_VENV)/bin/appworld" install
	cd "$(APPWORLD_ROOT)" && $(APPWORLD_ENV) "$(APPWORLD_VENV)/bin/appworld" download data
	touch "$(APPWORLD_DATA_MARKER)"

appworld-envs-oceanbase: appworld-envs
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" $(UV) sync --extra appworld-eval --extra oceanbase --extra langchain --extra openai

appworld-check: appworld-envs
	$(CONTEXTSEEK_PYTHON) -c "import pydantic, contextseek; print('contextseek python ok, pydantic=', pydantic.__version__)"
	cd "$(PROJECT_ROOT)" && $(APPWORLD_ENV) $(APPWORLD_PYTHON) -c "from appworld.task import load_task_ids; ids = load_task_ids('test_normal'); print('appworld python ok, tasks=', len(ids), ids[:3])"

appworld-bench-baseline: appworld-envs
	$(APPWORLD_RUN) --config eval/appworld/config/baseline.yaml --stage run,evaluate $(APPWORLD_NO_RESUME_FLAG)

appworld-bench-file: appworld-envs
	$(APPWORLD_RUN) --config eval/appworld/config/contextseek_store_only.yaml --stage run,distill $(APPWORLD_NO_RESUME_FLAG)
	$(APPWORLD_RUN) --config eval/appworld/config/contextseek_react.yaml --stage run,evaluate $(APPWORLD_NO_RESUME_FLAG)

appworld-bench-file-evolve: appworld-envs
	$(APPWORLD_RUN) --config eval/appworld/config/contextseek_evolve.yaml --stage run,evaluate $(APPWORLD_NO_RESUME_FLAG)

appworld-bench-oceanbase: appworld-envs-oceanbase
	$(APPWORLD_RUN) --config eval/appworld/config/contextseek_store_only_oceanbase.yaml --stage run,distill $(APPWORLD_NO_RESUME_FLAG)
	$(APPWORLD_RUN) --config eval/appworld/config/contextseek_react_oceanbase.yaml --stage run,evaluate $(APPWORLD_NO_RESUME_FLAG)

appworld-bench-all: appworld-bench-baseline appworld-bench-file appworld-bench-oceanbase

appworld-clean-envs:
	rm -rf "$(PROJECT_ROOT)/.venv" "$(APPWORLD_VENV)"

# ── tau-bench ─────────────────────────────────────────────────────────────────

taubench-source:
	@if [ ! -d "$(TAUBENCH_SOURCE)" ]; then \
		echo "Cloning tau2-bench to $(TAUBENCH_SOURCE)"; \
		GIT_TERMINAL_PROMPT=0 git clone --depth 1 --filter=blob:none "$(TAUBENCH_REPO)" "$(TAUBENCH_SOURCE)" || { \
			rm -rf "$(TAUBENCH_SOURCE)"; \
			echo "Retrying tau2-bench clone once..."; \
			GIT_TERMINAL_PROMPT=0 git clone --depth 1 --filter=blob:none "$(TAUBENCH_REPO)" "$(TAUBENCH_SOURCE)"; \
		}; \
	fi
	@if [ ! -f "$(TAUBENCH_SOURCE)/pyproject.toml" ] && [ ! -f "$(TAUBENCH_SOURCE)/setup.py" ]; then \
		echo "error: TAUBENCH_SOURCE=$(TAUBENCH_SOURCE) is not a Python project."; \
		echo "Set TAUBENCH_SOURCE to a valid tau2-bench checkout or remove it and rerun make."; \
		exit 1; \
	fi

taubench-install: taubench-source
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" UV_PROJECT_ENVIRONMENT="$(TAUBENCH_VENV)" $(UV) sync
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" $(UV) pip install --python "$(TAUBENCH_PYTHON)" -e "$(TAUBENCH_SOURCE)"

taubench-install-oceanbase: taubench-source
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" UV_PROJECT_ENVIRONMENT="$(TAUBENCH_VENV)" $(UV) sync --extra oceanbase --extra langchain --extra openai
	UV_HTTP_TIMEOUT="$(UV_HTTP_TIMEOUT)" $(UV) pip install --python "$(TAUBENCH_PYTHON)" -e "$(TAUBENCH_SOURCE)"

taubench-check: taubench-install
	cd "$(PROJECT_ROOT)" && $(TAUBENCH_ENV) $(TAUBENCH_PYTHON) -c "import eval.taubench.tau2_compat; import tau2; print('tau-bench python ok:', tau2.__file__)"

taubench-bench-baseline: taubench-install
	$(TAUBENCH_RUN) --config eval/taubench/config/baseline.yaml --stage run,evaluate

taubench-bench-store: taubench-install
	$(TAUBENCH_RUN) --config eval/taubench/config/store_only.yaml --stage run,distill

taubench-bench-react: taubench-install
	$(TAUBENCH_RUN) --config eval/taubench/config/contextseek_react.yaml --stage run,evaluate

taubench-bench-evolve: taubench-install
	$(TAUBENCH_RUN) --config eval/taubench/config/contextseek_evolve.yaml --stage run,evaluate

taubench-bench-store-oceanbase: taubench-install-oceanbase
	$(TAUBENCH_RUN) --config eval/taubench/config/store_only_oceanbase.yaml --stage run,distill

taubench-bench-react-oceanbase: taubench-install-oceanbase
	$(TAUBENCH_RUN) --config eval/taubench/config/contextseek_react_oceanbase.yaml --stage run,evaluate

taubench-bench-evolve-oceanbase: taubench-install-oceanbase
	$(TAUBENCH_RUN) --config eval/taubench/config/contextseek_evolve_oceanbase.yaml --stage run,evaluate

taubench-bench-oceanbase: taubench-bench-store-oceanbase taubench-bench-react-oceanbase taubench-bench-evolve-oceanbase

taubench-bench-all: taubench-bench-baseline taubench-bench-store taubench-bench-react taubench-bench-evolve taubench-bench-oceanbase

taubench-clean-env:
	rm -rf "$(TAUBENCH_VENV)" "$(TAUBENCH_SOURCE)"
