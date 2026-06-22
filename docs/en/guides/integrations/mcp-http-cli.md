# MCP, HTTP & CLI

ContextSeek exposes the same operations through three surfaces:

| Surface | When to use |
|---------|-------------|
| **Python SDK** | In-process agents and services |
| **CLI** | Scripts, cron, local debugging |
| **HTTP API** | Language-agnostic services, gateways |
| **MCP** | IDE agents (Cursor, Claude Desktop) and tool-calling hosts |

All paths call the same `ContextSeek` client logic (storage, retrieval, evolution).

---

## CLI (`contextseek`)

Installed with the package:

```bash
pip install contextseek
contextseek --help
```

Assumes `.env` in cwd or repo root (see [Configuration](../../getting-started/configuration.md)).

> For client-side / personal mode (`init`, `daemon`, `sync`, default SQLite) and the full command reference, see **[CLI (client-side)](../cli.md)**. Below is a quick cheat sheet only.

### Commands

| Command | Description |
|---------|-------------|
| `add` | Write one item |
| `retrieve` | Ranked search (`--k`, optional `--full`) |
| `expand` | Load L0 by comma-separated `--ids` |
| `compact` | Run evolution (`--dry-run`) |
| `forget` | Soft-delete (`--item-id`, `--reason`) |
| `delete` | Hard delete (`--no-propagate` optional) |
| `overview` | Stage counts + evolution hints |
| `dream` | Dream cycle (`--dry-run`) |
| `feedback` | Score delta (`--score`, `--reason`) |
| `upstream` | Upstream link walk |
| `evidence-chain` | Full evidence DAG (`--max-depth`) |
| `chain-confidence` | Aggregate confidence |
| `tools` | Print built-in LLM tool specs (`--format openai\|anthropic`) |
| `skill-tools` | Export skill-stage tools as definitions |
| `skill-context` | Render skill prompt block |
| `skill-import` | Import Hermes/OpenAI/MCP skill files |
| `items` | List scope items (`--stage` filter) |
| `metrics` | Prometheus text (if audit/metrics enabled) |

### Examples

```bash
# Write
contextseek add \
  --scope acme/proj/user \
  --content "Prefer concise answers in Chinese" \
  --source cli \
  --tags preference,language

# Search (L1 summaries)
contextseek retrieve \
  --scope acme/proj/user \
  --query "language preference" \
  --k 5

# Full L0 in one shot
contextseek retrieve \
  --scope acme/proj/user \
  --query "language preference" \
  --k 3 \
  --full

# Expand selected ids
contextseek expand --scope acme/proj/user --ids id1,id2

# Maintenance
contextseek overview --scope acme/proj/user
contextseek compact --scope acme/proj/user
contextseek dream --scope acme/proj/user --dry-run

contextseek forget --scope acme/proj/user --item-id abc123 --reason cleanup
contextseek delete --scope acme/proj/user --item-id abc123 --reason gdpr

# Provenance
contextseek upstream --scope acme/proj/user --item-id abc123
contextseek evidence-chain --scope acme/proj/user --item-id abc123 --max-depth 8
```

CLI output is **JSON** on stdout for machine parsing.

### Programmatic CLI

```python
from contextseek.cli.main import run_cli

exit_code = run_cli(["retrieve", "--scope", "t/p/u", "--query", "test", "--k", "5"])
```

Pass `client=my_seek_context` to inject a test double.

---

## HTTP API (FastAPI)

Install:

```bash
pip install "contextseek[http]"
```

Run:

```bash
uvicorn contextseek.http.server:app --host 0.0.0.0 --port 8000
# Or: make demo-http  (127.0.0.1:8000 with reload)
```

OpenAPI docs: `http://localhost:8000/docs`

### Routes

| Method | Path | Body highlights |
|--------|------|-----------------|
| `POST` | `/add` | `scope`, `content`, `source`, `tags[]` |
| `POST` | `/retrieve` | `scope`, `query`, `k`, `full`, `filters`, `include_deleted` |
| `POST` | `/expand` | `scope`, `ids[]` |
| `POST` | `/forget` | `scope`, `item_id`, `reason` |
| `POST` | `/delete` | `scope`, `item_id`, `reason`, `propagate` |
| `POST` | `/compact` | `scope`, `dry_run` |
| `POST` | `/dream` | `scope`, `dry_run` |
| `POST` | `/feedback` | `scope`, `item_id`, `score`, `reason` |
| `POST` | `/upstream` | `scope`, `item_id` |
| `POST` | `/evidence_chain` | `scope`, `item_id`, `max_depth` |
| `POST` | `/chain_confidence` | `scope`, `item_id` |
| `POST` | `/skill_tools` | `scope`, `fmt`, `query`, `k` |
| `POST` | `/skill_context` | `scope`, `query`, `k` |
| `POST` | `/items` | `scope`, optional `stage` |
| `GET` | `/overview?scope=...` | Query param `scope` |
| `GET` | `/metrics` | Prometheus text |
| `GET` | `/health` | `{"status":"ok","version":"<package>"}` (also in OpenAPI `info.version`) |

### curl examples

```bash
curl -s -X POST http://localhost:8000/add \
  -H "Content-Type: application/json" \
  -d '{"scope":"t/p/u","content":"hello","source":"curl","tags":["demo"]}'

curl -s -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"scope":"t/p/u","query":"hello","k":5,"full":false}'

curl -s -X POST http://localhost:8000/expand \
  -H "Content-Type: application/json" \
  -d '{"scope":"t/p/u","ids":["<item-id>"]}'

curl -s "http://localhost:8000/overview?scope=t/p/u"
curl -s http://localhost:8000/health
```

### Retrieve response shape

```json
{
  "items": [
    {
      "id": "...",
      "score": 0.82,
      "layer": "summary",
      "summary": "L1 text ...",
      "content": null,
      "provenance_summary": "source: document; id=wiki/1",
      "stage_confidence": 0.85,
      "recall_path": "phrase,term"
    }
  ],
  "_meta": {
    "layer": "summary",
    "full_via": "expand",
    "hint": "These items contain summaries only..."
  }
}
```

### Custom app embedding

```python
from contextseek import ContextSeek
from contextseek.http.server import create_app

ctx = ContextSeek.from_settings()
app = create_app(client=ctx)
```

---

## MCP server

Model Context Protocol exposes ContextSeek tools to hosts that speak JSON-RPC (Cursor, custom agents).

Install: `pip install contextseek` (HTTP extra optional for SSE transport).

### stdio (local IDE)

```bash
contextseek-mcp-stdio
```

Configure in MCP client JSON (example):

```json
{
  "mcpServers": {
    "contextseek": {
      "command": "contextseek-mcp-stdio",
      "env": {
        "STORAGE_BACKEND": "file",
        "STORAGE_PATH": "/path/to/.contextseek/data"
      }
    }
  }
}
```

Environment variables are read the same way as the SDK (`.env` + MCP `env` block).

### SSE (remote)

```bash
contextseek-mcp-sse --host 0.0.0.0 --port 8001
```

Requires `contextseek[http]` (FastAPI). Clients connect to the SSE endpoint exposed by `MCPRuntime`.

### MCP tools

| Tool | SDK equivalent |
|------|----------------|
| `contextseek_add` | `add()` |
| `contextseek_retrieve` | `retrieve()` (`k`, `full`) |
| `contextseek_expand` | `expand_by_ids()` |
| `contextseek_forget` | `forget()` |
| `contextseek_delete` | `delete()` |
| `contextseek_compact` | `compact()` |
| `contextseek_dream` | `dream()` |
| `contextseek_overview` | `overview()` |
| `contextseek_feedback` | `feedback()` |
| `contextseek_upstream` | `upstream()` |
| `contextseek_evidence_chain` | `evidence_chain()` |
| `contextseek_chain_confidence` | `chain_confidence()` |
| `contextseek_skill_tools` | `skill_tools()` |
| `contextseek_skill_context` | `skill_context()` |
| `contextseek_items` | `items()` |

### Agent workflow with MCP

1. `contextseek_retrieve` with user question → summaries + ids in JSON.
2. Model decides which ids need detail → `contextseek_expand`.
3. Optional: `contextseek_feedback` on ids that helped.
4. Periodic: `contextseek_compact` on long-running scopes.

### Python embedding

```python
from contextseek import ContextSeek
from contextseek.mcp.server import ContextSeekMCPServer
from contextseek.mcp.runtime import MCPRuntime

server = ContextSeekMCPServer(client=ContextSeek.from_settings())
runtime = MCPRuntime(server=server)
response = runtime.handle_request({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "contextseek_retrieve", "arguments": {"scope": "t/p/u", "query": "test"}},
})
```

---

## Choosing a surface

| Requirement | Pick |
|-------------|------|
| Lowest latency, full Python | SDK |
| Ops / CI automation | CLI |
| Polyglot microservice | HTTP |
| Cursor / MCP-native agent | MCP stdio |
| Remote MCP consumer | MCP SSE |

Behavior differences are limited to serialization—always use the same **`scope`** convention across surfaces.

---

## Security notes

- HTTP and SSE should sit behind auth (API gateway, mTLS) in production.
- MCP inherits process env—do not pass production keys to untrusted hosts.
- CLI and MCP respect `SECURITY_*` ACL settings when enabled.

## Related

- [Write & retrieve](../write-and-retrieve.md)
- [Configuration](../../getting-started/configuration.md)
- [Observability](../observability.md) — audit + metrics endpoints
