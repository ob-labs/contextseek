# ContextSeek + τ-bench Evaluation

τ-bench (tau2) evaluation harness for ContextSeek.

## Quick Start

```bash
# Set up credentials. OPENAI_API_BASE/OPENAI_BASE_URL are optional overrides
# for OpenAI-compatible API gateways.
export OPENAI_API_KEY=your-key
export OPENAI_API_BASE=https://your-gateway/v1  # optional
export OPENAI_BASE_URL=https://your-gateway/v1   # optional

# Create/update the isolated tau-bench environment.
make taubench-install

# Optional sanity check.
make taubench-check

# 1. Baseline (no ContextSeek)
make taubench-bench-baseline

# 2. Warm-up (store trajectories in ContextSeek)
make taubench-bench-store

# 3. React (retrieve + write)
make taubench-bench-react

# 4. Evolution (retrieve + auto-compact)
make taubench-bench-evolve

# Optional: OceanBase-backed store + react + evolve.
make taubench-bench-oceanbase

# Run all tau-bench configs in order.
make taubench-bench-all
```

The Makefile uses an isolated `.venv-taubench` environment via `UV_PROJECT_ENVIRONMENT`, then installs tau2 from a local checkout at `.tau2-bench`. If that checkout does not exist, `make taubench-install` clones `https://github.com/sierra-research/tau2-bench.git` automatically. tau-bench dependencies do not overwrite the AppWorld evaluation environment. To recreate everything from scratch:

```bash
make taubench-clean-env
make taubench-install
```

To use an existing checkout instead of the default path:

```bash
make taubench-install TAUBENCH_SOURCE=/path/to/tau2-bench
```

If dependency downloads are slow, override the uv HTTP timeout:

```bash
make taubench-install UV_HTTP_TIMEOUT=600
```

## OceanBase Backend

The default tau-bench configs use the file backend. To run the OceanBase storage
variant, set the database and embedding environment variables first:

```bash
export OB_HOST=127.0.0.1
export OB_PORT=2881
export OB_USER=root@test
export OB_PASSWORD=your-password
export OB_DB_NAME=contextseek

export OPENAI_API_KEY=your-key
export EMBEDDING_BASE_URL=https://your-gateway/v1  # optional
```

Then run:

```bash
# Install OceanBase + LangChain embedding dependencies in .venv-taubench.
make taubench-install-oceanbase

# Warm up OceanBase, then evaluate retrieval and evolution.
make taubench-bench-oceanbase
```

The OceanBase targets use:

| Target | Config |
|--------|--------|
| `make taubench-bench-store-oceanbase` | `store_only_oceanbase.yaml` |
| `make taubench-bench-react-oceanbase` | `contextseek_react_oceanbase.yaml` |
| `make taubench-bench-evolve-oceanbase` | `contextseek_evolve_oceanbase.yaml` |

## Manual Commands

The Makefile targets above expand to these direct commands:

```bash
# 1. Baseline (no ContextSeek)
.venv-taubench/bin/python -m eval.taubench.run \
  --config eval/taubench/config/baseline.yaml \
  --stage run,evaluate

# 2. Warm-up (store trajectories in ContextSeek)
.venv-taubench/bin/python -m eval.taubench.run \
  --config eval/taubench/config/store_only.yaml \
  --stage run,distill

# 3. React (retrieve + write)
.venv-taubench/bin/python -m eval.taubench.run \
  --config eval/taubench/config/contextseek_react.yaml \
  --stage run,evaluate

# 4. Evolution (retrieve + auto-compact)
.venv-taubench/bin/python -m eval.taubench.run \
  --config eval/taubench/config/contextseek_evolve.yaml \
  --stage run,evaluate
```

If you choose not to use the isolated Makefile environment, install tau2 in your active environment first:

```bash
uv pip install -e ./.tau2-bench
python -m eval.taubench.run \
  --config eval/taubench/config/baseline.yaml \
  --stage run,evaluate
```

## Experiment Groups

| Config | Purpose | ContextSeek |
|--------|---------|-------------|
| `baseline.yaml` | Native agent, no SC | None |
| `store_only.yaml` | Warm-up phase | Write only |
| `contextseek_react.yaml` | Retrieval-enhanced | Retrieve + Write |
| `contextseek_evolve.yaml` | Retrieval + Evolution | Retrieve + Write + Compact |
| `store_only_oceanbase.yaml` | OceanBase warm-up phase | Write only |
| `contextseek_react_oceanbase.yaml` | OceanBase retrieval-enhanced | Retrieve + Write |
| `contextseek_evolve_oceanbase.yaml` | OceanBase retrieval + Evolution | Retrieve + Write + Compact |

## Output Structure

```
output/taubench/<experiment>/
├── config_snapshot.json
├── trajectories/
│   └── <adapter>.jsonl
├── distill/
│   └── distill.log
└── evaluate/
    ├── report.md
    └── summary.json
```