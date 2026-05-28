PYTHON ?= python3
UV ?= uv
UVICORN ?= $(PYTHON) -m uvicorn

.PHONY: help \
	install install-http install-all install-hooks \
	test test-unit test-integration test-cov \
	lint format check \
	build clean \
	demo-langchain demo-http

help:
	@echo "Available targets:"
	@echo ""
	@echo "Install:"
	@echo "  make install        # Base dependencies"
	@echo "  make install-http   # + HTTP server extras"
	@echo "  make install-all    # All extras (langchain, openai, oceanbase, http)"
	@echo "  make install-hooks  # Enable git pre-commit auto-format hook"
	@echo ""
	@echo "Test:"
	@echo "  make test               # All tests"
	@echo "  make test-unit          # Unit tests only"
	@echo "  make test-integration   # Integration tests only"
	@echo "  make test-cov           # All tests with coverage report"
	@echo ""
	@echo "Code quality:"
	@echo "  make lint     # ruff check"
	@echo "  make format   # ruff format"
	@echo "  make check    # lint + format check (no writes)"
	@echo ""
	@echo "Build:"
	@echo "  make build   # Build wheel + sdist"
	@echo "  make clean   # Remove build artifacts and caches"
	@echo ""
	@echo "Demo:"
	@echo "  make demo-langchain   # Run LangChain-style ContextSeek demo"
	@echo "  make demo-http        # Start FastAPI server at 127.0.0.1:8000"
	@echo ""
	@echo "Benchmark targets are in eval/Makefile:"
	@echo "  make -f eval/Makefile help"

# ── Install ───────────────────────────────────────────────────────────────────

install:
	$(UV) sync

install-http:
	$(UV) sync --extra http

install-all:
	$(UV) sync --extra http --extra langchain --extra openai --extra oceanbase

install-hooks:
	git config core.hooksPath .githooks

# ── Test ──────────────────────────────────────────────────────────────────────

test:
	$(UV) run pytest -q

test-unit:
	$(UV) run pytest -q tests/unit_tests/

test-integration:
	$(UV) run pytest -q tests/integration_tests/

test-cov:
	$(UV) run pytest -q --cov=src/contextseek --cov-report=term-missing

# ── Code quality ──────────────────────────────────────────────────────────────

lint:
	$(UV) run ruff check src/ tests/

format:
	$(UV) run ruff format src/ tests/

check:
	$(UV) run ruff check src/ tests/
	$(UV) run ruff format --check src/ tests/

# ── Build ─────────────────────────────────────────────────────────────────────

build: clean
	$(UV) run --with build --with hatchling python -m build --sdist --wheel --no-isolation

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf dist/ build/ .coverage htmlcov/

# ── Demo ──────────────────────────────────────────────────────────────────────

demo-langchain:
	PYTHONPATH=src $(PYTHON) examples/langchain_pipeline.py

demo-http:
	PYTHONPATH=src $(UVICORN) contextseek.http.server:app --host 127.0.0.1 --port 8000 --reload
