PYTHON ?= python3
UV ?= uv
UVICORN ?= $(PYTHON) -m uvicorn

.PHONY: help \
	install install-http install-all install-hooks \
	test test-unit test-integration test-cov \
	lint format check \
	build clean \
	bump \
	demo-langchain \
	backend frontend

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
	@echo "Release:"
	@echo "  make bump VERSION=X.Y.Z   # Set version in pyproject.toml + refresh uv.lock"
	@echo ""
	@echo "Demo:"
	@echo "  make demo-langchain   # Run LangChain-style ContextSeek demo"
	@echo ""
	@echo "Dev servers:"
	@echo "  make backend          # Start API server at 127.0.0.1:8000 (with --reload)"
	@echo "  make frontend         # Build + serve SPA at 127.0.0.1:3000 (needs backend)"
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

# ── Release ───────────────────────────────────────────────────────────────────

# Bump the single source of truth (pyproject.toml) and refresh the lockfile.
# Usage: make bump VERSION=0.1.3
bump:
	@test -n "$(VERSION)" || { echo "error: VERSION is required, e.g. make bump VERSION=0.1.3"; exit 1; }
	@echo "$(VERSION)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([abc]|rc|\.post|\.dev)?[0-9]*$$' \
		|| { echo "error: VERSION '$(VERSION)' is not a valid PEP 440 version (expected X.Y.Z)"; exit 1; }
	$(PYTHON) -c "import re,pathlib; p=pathlib.Path('pyproject.toml'); t=p.read_text(); t,n=re.subn(r'(?m)^version = \".*\"', 'version = \"$(VERSION)\"', t, count=1); assert n==1, 'version line not found in pyproject.toml'; p.write_text(t)"
	$(UV) lock
	@echo "Bumped to $(VERSION). Review with: git diff pyproject.toml uv.lock"
	@echo "Next: commit, merge to main, then tag v$(VERSION) to trigger the release workflow."

# ── Demo ──────────────────────────────────────────────────────────────────────

demo-langchain:
	PYTHONPATH=src $(PYTHON) examples/langchain_pipeline.py

backend:
	PYTHONPATH=src $(UVICORN) contextseek.http.server:app --host 127.0.0.1 --port 8000 --reload

frontend:
	@command -v npm >/dev/null 2>&1 || { echo "npm is required (install Node.js)"; exit 2; }
	npm --prefix dashboard install
	npm --prefix dashboard run build
	npm --prefix dashboard run preview
