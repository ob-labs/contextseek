# Settings Reference

All settings are loaded from environment variables and an optional `.env` file. The zero-config default is a local SQLite store with keyword-only retrieval — no API keys required.

Copy `.env.example` to `.env` and edit. See [Configuration](../getting-started/configuration.md) for resolution order and code-level construction.

---

## Storage (`STORAGE_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `sqlite` | Storage backend: `sqlite`, `memory`, `file`, `seekdb`, or `oceanbase` |
| `STORAGE_PATH` | `.contextseek/store` | Root directory when `backend=file` |
| `SQLITE_PATH` | `~/.contextseek/contextseek.sqlite3` | SQLite file when `backend=sqlite` |
| `STORAGE_URI_SCHEME` | `contextseek://` | URI scheme used for scope refs |
| `STORAGE_COLD_BACKEND` | _(empty)_ | Optional cold-tier backend type; empty disables tiered storage |
| `STORAGE_COLD_PATH` | `.contextseek/cold` | Root directory for cold-tier file backend |

For OceanBase see the `OB_*` section below and [Storage backends](../guides/storage.md).

## OceanBase (`OB_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OB_HOST` | `127.0.0.1` | OceanBase host |
| `OB_PORT` | `2881` | OceanBase port |
| `OB_USER` | `root@test` | Connection user |
| `OB_PASSWORD` | _(empty)_ | Connection password |
| `OB_DB_NAME` | `test` | Database name |
| `OB_TABLE_NAME` | `contextseek_items` | Vector table name |

OceanBase is instantiated via the runtime factory or examples — these vars are not read by the default `from_settings()` path unless you call `from_runtime_config()`.

## Embedding (`EMBEDDING_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `none` | `none`, `openai`, `dashscope`, `ollama`, `huggingface`, or `langchain` |
| `EMBEDDING_CLASS_PATH` | _(empty)_ | Optional custom LangChain class, e.g. `langchain_openai.OpenAIEmbeddings` |
| `EMBEDDING_MODEL` | _(empty)_ | Model name passed to the provider constructor |
| `EMBEDDING_DIMS` | `0` | Vector dimensions; inferred for known providers when omitted |
| `EMBEDDING_KWARGS` | `{}` | Extra kwargs forwarded to the provider constructor (JSON object) |

Provider API keys (`OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, etc.) are read directly by the LangChain class, not by ContextSeek.

## LLM (`LLM_*`)

Shared LLM client used by: reranker, summarizer, evolution engine, dream engine, conflict judge.

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `none` | `none`, `openai`, `dashscope`, `ollama`, or `langchain` |
| `LLM_CLASS_PATH` | _(empty)_ | Optional custom LangChain class, e.g. `langchain_openai.ChatOpenAI` |
| `LLM_MODEL` | _(empty)_ | Chat model name |
| `LLM_KWARGS` | `{}` | Extra kwargs forwarded to the provider constructor (JSON object) |

Use `provider=langchain` with `*_CLASS_PATH` for custom LangChain classes.

## Summarizer (`SUMMARIZER_*`)

Drives L2 `abstract` and L1 `summary` generation on every `add()`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SUMMARIZER_PROVIDER` | `llm` | `none` (disabled) or `llm` (uses `LLM_*`) |
| `SUMMARIZER_L2_MAX_CHARS` | `100` | Character budget for L2 abstract |
| `SUMMARIZER_L1_MAX_CHARS` | `2000` | Character budget for L1 summary |

When `SUMMARIZER_PROVIDER=llm` but no LLM is configured, the summarizer is skipped and a one-time warning is emitted. Retrieval falls back to L0-only behavior.

## Retrieval (`RETRIEVAL_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `RETRIEVAL_DEFAULT_K` | `20` | Default candidate pool size |
| `RETRIEVAL_RECALL_ROUTES` | `["phrase","terms"]` | JSON list: `phrase`, `terms`, `vector` |
| `RETRIEVAL_CANDIDATE_MULTIPLIER` | `4` | Oversampling factor before rerank |
| `RETRIEVAL_VECTOR_WEIGHT` | `0.7` | Hybrid score weight for vector similarity |
| `RETRIEVAL_FTS_WEIGHT` | `0.3` | Hybrid score weight for full-text search |
| `RETRIEVAL_TERM_WEIGHT` | `0.15` | Term overlap contribution to heuristic score |
| `RETRIEVAL_RECENCY_WEIGHT` | `0.05` | Recency contribution to heuristic score |
| `RETRIEVAL_FEEDBACK_WEIGHT` | `0.20` | `relevance_boost` contribution to heuristic score |
| `RETRIEVAL_ARCHIVE_PENALTY` | `0.50` | Score multiplier for archived items |
| `RETRIEVAL_PROVENANCE_WEIGHT` | `0.15` | Provenance confidence contribution |
| `RETRIEVAL_LINK_BOOST` | `0.10` | Score bonus for items with supporting links |
| `RETRIEVAL_LINK_REFUTE_PENALTY` | `0.40` | Score penalty for items with refuting links |
| `RETRIEVAL_LINK_SUPERSEDE_PENALTY` | `0.35` | Score penalty for superseded items |
| `RETRIEVAL_RERANKER_MODE` | `heuristic` | `heuristic` or `llm` |
| `RETRIEVAL_LLM_RERANK_TOP_N` | `20` | Candidate count passed to LLM reranker |

## Evolution (`EVOLUTION_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `EVOLUTION_ENABLED` | `false` | Master switch — enables full `compact()` pipeline |
| `EVOLUTION_DEDUPE_BY_HASH` | `true` | Hash-based exact deduplication on `compact()` |
| `EVOLUTION_SEMANTIC_MERGE` | `true` | Similarity-based cluster merge on `compact()` |
| `EVOLUTION_SEMANTIC_MERGE_THRESHOLD` | `0.72` | Cosine similarity threshold for merge clustering |
| `EVOLUTION_MIN_CLUSTER_SIZE` | `3` | Minimum items required to form a merge cluster |
| `EVOLUTION_DECAY_HALF_LIFE_DAYS` | `7.0` | Half-life for importance decay (days) |
| `EVOLUTION_EXTRACT_MIN_AGE_SECONDS` | `60.0` | Minimum item age before extraction is attempted |
| `EVOLUTION_DISTILL_MIN_USE_COUNT` | `10` | Minimum `access_count` before skill distillation |
| `EVOLUTION_DISTILL_MIN_RELEVANCE_BOOST` | `1.2` | Minimum `relevance_boost` before distillation |
| `EVOLUTION_EPHEMERAL_TTL_SECONDS` | `3600.0` | TTL for ephemeral items before archival |
| `EVOLUTION_LLM_MERGE_ENABLED` | `false` | LLM synthesis for cluster merge |
| `EVOLUTION_LLM_CONFLICT_CHECK_ENABLED` | `false` | LLM contradiction judge on write |
| `EVOLUTION_LLM_STAGE_INFER_ENABLED` | `false` | LLM stage classification on write |
| `EVOLUTION_LLM_DISTILL_ENABLED` | `false` | LLM skill distillation |
| `EVOLUTION_LLM_FEEDBACK_ENABLED` | `false` | LLM feedback reason parsing |

## Dream (`DREAM_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DREAM_LLM_ENABLED` | `false` | LLM-assisted consolidation and divergence in dream cycles |

## Prompts (`PROMPT_*`)

Override any LLM prompt template. Placeholders vary by template (`{query}`, `{content}`, `{items}`, etc.). Escape literal braces in JSON-embedded examples as `{{` and `}}`.

| Variable | Template purpose |
|----------|-----------------|
| `PROMPT_SUMMARIZER_ABSTRACT_TEMPLATE` | L2 abstract generation |
| `PROMPT_SUMMARIZER_SUMMARY_TEMPLATE` | L1 summary generation |
| `PROMPT_RETRIEVAL_RELEVANCE_TEMPLATE` | LLM reranker scoring |
| `PROMPT_CONFLICT_JUDGE_TEMPLATE` | Contradiction detection on write |
| `PROMPT_STAGE_CLASSIFIER_TEMPLATE` | LLM stage inference on write |
| `PROMPT_FEEDBACK_TAG_TEMPLATE` | Feedback reason parsing |
| `PROMPT_MERGE_SYNTHESIS_TEMPLATE` | Cluster merge synthesis |
| `PROMPT_DISTILL_CANDIDATE_TEMPLATE` | Skill distillation candidate scoring |
| `PROMPT_DISTILL_RENDER_TEMPLATE` | Skill distillation rendering |
| `PROMPT_DREAM_CONSOLIDATION_TEMPLATE` | Dream consolidation step |
| `PROMPT_DREAM_DIVERGENCE_TEMPLATE` | Dream divergence/hypothesis step |

See commented blocks in `.env.example` for all template keys and their placeholder variables.

## Security (`SECURITY_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SECURITY_ACL_ENABLED` | `true` | Enforce read/write ACL policies |
| `SECURITY_ALLOW_ANY_SOURCE` | `true` | When `false`, only `SECURITY_ALLOWED_SOURCES` may write |
| `SECURITY_ALLOWED_SOURCES` | `[]` | JSON list of allowed source identifiers |
| `SECURITY_REDACT_SENSITIVE` | `false` | Redact matched fields on write |
| `SECURITY_REDACTION_TOKEN` | `[REDACTED]` | Replacement token for redacted values |
| `SECURITY_REDACT_FIELDS` | `[]` | JSON list of field names to redact |
| `SECURITY_DROP_FIELDS` | `[]` | JSON list of field names to drop entirely |

## Observability (`OBSERVABILITY_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSERVABILITY_AUDIT_ENABLED` | `false` | Enable JSONL audit log |
| `OBSERVABILITY_AUDIT_PATH` | `.contextseek/audit.jsonl` | Audit log file path |
| `OBSERVABILITY_METRICS_ENABLED` | `false` | Enable Prometheus text metrics export |
| `OBSERVABILITY_METRICS_PATH` | `.contextseek/metrics.prom` | Metrics file path |
| `OBSERVABILITY_TRACE_SAMPLE_RATE` | `1.0` | Fraction of requests to trace (0.0–1.0) |

## Lifecycle (`LIFECYCLE_*`)

| Variable | Default | Description |
|----------|---------|-------------|
| `LIFECYCLE_INTERVAL_SECONDS` | `3600.0` | Scheduler check interval (seconds) |
| `LIFECYCLE_AUTO_COMPACT` | `true` | Allow scheduler to trigger compaction |
| `LIFECYCLE_COMPACT_MIN_ITEMS` | `5` | Minimum item count before compaction runs |

## Scope lint

| Variable | Default | Description |
|----------|---------|-------------|
| `SCOPE_LINT` | `false` | Check scope strings on every `ctx.add()` call and emit `ScopeLintWarning` for malformed scopes |

When `SCOPE_LINT=true`, the following rules are checked:

| Condition | Warning |
|-----------|---------|
| Empty scope string | Strongly recommend using a hierarchical scope |
| No `/` separator (flat scope) | At least two levels recommended for isolation |
| Depth > 6 levels | Too deep — may narrow retrieval to near-zero results |
| Uppercase letters or spaces | Use lowercase kebab-case |

This check is off by default; enable it during development only. You can also configure it in code:

```python
from contextseek import ContextSeek
from contextseek.config.settings import ContextSeekSettings

ctx = ContextSeek.from_settings(ContextSeekSettings(scope_lint=True))
```

---

## Quick reference: minimal production `.env`

```env
# Storage
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/data

# Embeddings (OpenAI example)
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...

# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini

# Retrieval
RETRIEVAL_RECALL_ROUTES=["phrase","terms","vector"]

# Phase 1 LLM features
RETRIEVAL_RERANKER_MODE=llm
RETRIEVAL_LLM_RERANK_TOP_N=20
DREAM_LLM_ENABLED=true

# Observability
OBSERVABILITY_AUDIT_ENABLED=true

# Evolution (enable after Phase 1 is stable)
EVOLUTION_ENABLED=true
```

See [Phased LLM rollout](../getting-started/configuration.md#phased-llm-rollout) before enabling all LLM features at once.

---

[← Configuration](../getting-started/configuration.md) · [API reference](api.md) · [Storage backends](../guides/storage.md)
