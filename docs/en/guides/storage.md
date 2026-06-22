# Storage backends

ContextSeek is storage-agnostic. Choose a backend based on your deployment stage; switching later requires only a configuration change, no code changes.

---

## InMemoryBackend

The default when no `STORAGE_BACKEND` is set. All data lives in a Python dict and is lost when the process exits.

**When to use:** unit tests, quick prototyping, single-request pipelines where persistence is not needed.

```python
from contextseek import ContextSeek

ctx = ContextSeek()  # InMemory by default
```

Or explicitly via settings:

```env
STORAGE_BACKEND=memory
```

---

## FileBackend

Persists every `ContextItem` as a JSON file under a local directory. Backed by [seekvfs](https://github.com/oceanbase/seekvfs).

**When to use:** local development, single-process agents, CI pipelines that need trace persistence between runs.

```env
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/store
```

The directory is created automatically on first write. Each scope maps to a sub-directory; each item is a single `.json` file named by item ID.

```python
from contextseek import ContextSeek, ContextSeekSettings
from contextseek.config.settings import StorageSettings

ctx = ContextSeek.from_settings(
    ContextSeekSettings(
        storage=StorageSettings(backend="file", path="/data/my-agent")
    )
)
```

**Embedding support:** vector recall is not available with `FileBackend`. Configure an embedder and switch to OceanBase when semantic search is required.

---

## OceanBase backend

Production backend with HNSW approximate nearest-neighbour vector search and full-text search (FTS), supporting hybrid recall out of the box.

**When to use:** multi-process deployments, production agents, any scenario requiring vector recall or high-throughput writes.

**Install the extra:**

```bash
pip install contextseek[oceanbase]
```

**Configuration:**

```env
STORAGE_BACKEND=oceanbase
STORAGE_OB_URI=mysql+pymysql://user:pass@host:2881/dbname
STORAGE_OB_TABLE=context_items
```

Pair with an embedding provider so `retrieve()` uses hybrid recall:

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...
```

The retrieval orchestrator automatically activates the `vector` recall route when an embedder is present.

---

## Tiered storage (hot + cold)

The `TieredSeekVFSAdapter` wraps two backends: a hot tier for recent/active items and a cold tier for archived content.

```env
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/hot
STORAGE_COLD_BACKEND=file
STORAGE_COLD_PATH=.contextseek/cold
```

Writes always go to the hot tier. `compact()` moves archived items (ephemeral TTL expired, soft-deleted, low-importance) to cold. The cold tier is read during `retrieve()` unless `include_deleted=False` (default).

> **Note:** automatic hot→cold promotion runs only during `compact()`. Items do not migrate on their own.

---

## Hash-based lookup

Every serialized `ContextItem` carries a content hash. Storage adapters that
can index that field expose `find_by_hash(prefix, hash_value)` as an optional
fast path for exact lookups within a scope prefix.

ContextSeek uses this lookup before full conflict detection when adding new
items. If the same content already exists in the same scope, the write can
return the existing item idempotently instead of scanning the whole scope or
creating a duplicate. Import and sync flows can also reuse hashes to skip
unchanged records across repeated runs.

Backends that cannot provide an efficient hash index may omit the fast path;
callers fall back to the normal scan/search behavior. Hash lookup is for exact
content reuse and deduplication, not semantic similarity.

---

## Choosing a backend

| Scenario | Recommended backend |
|---|---|
| Unit tests / CI | `InMemoryBackend` |
| Local dev, single process | `FileBackend` |
| seekdb embedded mode | `STORAGE_BACKEND=seekdb`|
| seekdb server mode | `STORAGE_BACKEND=oceanbase` |
| Semantic / vector search | OceanBase |
| Production multi-process | OceanBase |
| Long-term archival | Tiered (File + File, or OceanBase + File) |

---

## Embedding requirements for vector recall

Vector recall requires both:

1. A configured embedding provider (for example, `EMBEDDING_PROVIDER=openai`)
2. A backend that stores and queries vectors (OceanBase; `FileBackend` falls back to keyword-only recall)

If `EMBEDDING_PROVIDER=none` (default), `retrieve()` uses phrase and term matching only.

---

[← Write & retrieve](../write-and-retrieve.md) · [Configuration](../../getting-started/configuration.md) · [API reference](../../reference/api.md)
