# Installation

ContextSeek requires **Python 3.11, 3.12, or 3.13**. The core wheel includes the SDK, CLI, and MCP entry points. No API keys are required for the default SQLite backend.

## System requirements

| Component | Requirement |
|-----------|-------------|
| Python | ≥ 3.11 |
| OS | Linux, macOS, Windows (WSL recommended for production paths) |
| Disk | Default SQLite uses `SQLITE_PATH`; plan space under `STORAGE_PATH` for `file` |
| Network | Only if using remote embedding/LLM APIs or OceanBase |

## Install from PyPI

```bash
python3 --version   # should be 3.11+
pip install -U pip
pip install contextseek
```

Verify:

```bash
contextseek --help
python -c "from contextseek import ContextSeek; print(ContextSeek.from_settings())"
```

Installed console scripts:

| Command | Package role |
|---------|----------------|
| `contextseek` | CLI (`add`, `retrieve`, `expand`, `compact`, …) |
| `contextseek-mcp-stdio` | MCP over stdio |
| `contextseek-mcp-sse` | MCP over SSE (pass `--port`) |

## Optional extras (detailed)

Extras are [PEP 508](https://peps.python.org/pep-0508/) dependency groups. Combine with commas inside quotes.

| Extra | Pulls in (high level) | Install |
|-------|------------------------|---------|
| *(core)* | `seekvfs`, `pydantic`, `pydantic-settings`, `pyyaml` | `pip install contextseek` |
| `http` | FastAPI, Uvicorn | `pip install contextseek[http]` |
| `langchain` | `langchain-core` | `pip install contextseek[langchain]` |
| `openai` | `langchain-openai` | `pip install contextseek[langchain,openai]` |
| `ollama` | `langchain-ollama` | `pip install contextseek[langchain,ollama]` |
| `huggingface` | `langchain-huggingface` | `pip install contextseek[langchain,huggingface]` |
| `dashscope` | `langchain-community`, DashScope SDK | `pip install contextseek[langchain,dashscope]` |
| `oceanbase` | `pyobvector`, SQLAlchemy | `pip install contextseek[oceanbase]` |
| `test` | `pytest` | `pip install contextseek[test]` |

### Recommended bundles

```bash
# Local agent + REST API
pip install "contextseek[http,langchain,openai]"

# OceanBase deployment
pip install "contextseek[oceanbase,langchain,openai,http]"

# OceanBase deployment with DashScope embeddings
pip install "contextseek[oceanbase,langchain,dashscope,http]"

# Contributing to the repo
pip install -e ".[test]"
```

> **LangChain note:** `langchain` alone does not install a chat or embedding implementation. Always add `openai`, `ollama`, `huggingface`, or `dashscope` (or install those packages manually) before setting `EMBEDDING_PROVIDER` / `LLM_PROVIDER` to that provider.

## Install from source

### With uv (recommended for contributors)

```bash
git clone https://github.com/ob-labs/contextseek.git
cd contextseek
uv sync                    # creates .venv + installs from uv.lock
source .venv/bin/activate
uv run pytest tests/ -q    # 140+ tests, no .env required
```

### With pip editable

```bash
git clone https://github.com/ob-labs/contextseek.git
cd contextseek
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest tests/ -q
```

Run examples without installing the wheel globally:

```bash
uv run python examples/full_pipeline_file.py
```

## Dependency: seekvfs

`contextseek` depends on **[seekvfs](https://github.com/oceanbase/seekvfs)** (virtual filesystem abstraction). It is installed automatically as a normal dependency. You do not configure seekvfs separately unless you build custom storage adapters.

## Virtual environments and Docker

**venv / conda:** Always activate the environment where `contextseek` was installed before running CLI or examples.

**Docker sketch:**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install "contextseek[http,langchain,openai]"
ENV STORAGE_BACKEND=file
ENV STORAGE_PATH=/data/contextseek
CMD ["uvicorn", "contextseek.http.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Mount `/data/contextseek` as a volume for persistence. Inject `OPENAI_API_KEY` at runtime, not in the image layer.

## Troubleshooting install

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `contextseek: command not found` | Script dir not on `PATH` | Use `python -m` from venv or `pip install --user` path |
| `No module named 'contextseek'` | Wrong interpreter | `which python` inside activated venv |
| Import error for `langchain_openai` | Missing `openai` extra | `pip install contextseek[openai]` |
| Tests fail with OpenAI auth | Local `.env` loads LLM keys | Rename `.env` temporarily or unset `LLM_PROVIDER` |
| OceanBase import fails | Missing extra | `pip install contextseek[oceanbase]` |

## Security note

Do not bake API keys into images or commit `.env`. The repository `.gitignore` excludes `.env`; use your platform’s secret manager in production.

## Next steps

1. [Quickstart](quickstart.md) — first write and retrieve  
2. [Configuration](configuration.md) — `.env` profiles and LLM rollout  
3. [Core concepts](../guides/core-concepts.md) — model deep dive  
4. [Write & retrieve](../guides/write-and-retrieve.md) — full API behavior  
