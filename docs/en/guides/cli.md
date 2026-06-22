# CLI (Client-side / Personal mode)

The `contextseek` CLI is ContextSeek's client-side entry point: run a zero-dependency personal knowledge base on your own machine with local SQLite by default, let a background `daemon` auto-evolve and auto-sync your notes, and expose the same capabilities to Claude Desktop, Cursor, and other tools over MCP.

Every command runs on the same `ContextSeek` client logic underneath — the CLI, SDK, HTTP, and MCP paths are fully equivalent (see [MCP / HTTP / CLI](integrations/mcp-http-cli.md)).

---

## Install

```bash
# Default client-side use: SQLite (no external service / native engine)
pip install contextseek

# Add background file watching (daemon's WATCH_PATHS auto-sync)
pip install "contextseek[daemon]"

# Everything (HTTP / MCP / LangChain / seekdb / watchdog)
pip install "contextseek[all]"
```

Installation provides three executables:

| Command | Purpose |
|---------|---------|
| `contextseek` | Main CLI (this doc) |
| `contextseek-mcp-stdio` | Local stdio MCP server |
| `contextseek-mcp-sse` | Remote SSE MCP server (needs `http` extra) |

---

## Five-minute start

```bash
# 1. Initialize ~/.contextseek/ (config + register the background service)
contextseek init

# 2. Confirm the daemon is running
contextseek daemon status

# 3. Import existing notes / docs (format auto-detected)
contextseek sync ~/notes --scope me/work

# 4. Write one context item
contextseek add --scope me/work --content "Team code review is done in English" --tags convention

# 5. Retrieve (L1 summaries by default)
contextseek retrieve --scope me/work --query "code review convention"

# 6. See the whole scope
contextseek overview --scope me/work
```

> The default SQLite configuration does not require embeddings; configure `EMBEDDING_*` to enable vector retrieval.

---

## The `~/.contextseek/` directory

`contextseek init` creates and initializes the client-side working directory:

```
~/.contextseek/
├── config.env            # client-side config (equivalent to a project .env)
├── mcp.json              # MCP snippet to paste into your AI tool
├── contextseek.sqlite3   # SQLite data file
├── daemon.pid            # background process PID
├── daemon.status.json    # background component state
├── logs/
│   └── lifecycle.jsonl   # evolution / sync lifecycle log
├── backups/              # automatic pre-evolution snapshots
└── skills/               # (optional) exported SKILL.md files
```

The config location can be overridden with the `CONTEXTSEEK_CONFIG` environment variable (the systemd / launchd service uses it to point at `config.env`).

### Key config.env settings

| Key | Default | Description |
|-----|---------|-------------|
| `STORAGE_BACKEND` | `sqlite` | Default local SQLite; use `oceanbase` for seekdb server mode |
| `SQLITE_PATH` | `~/.contextseek/contextseek.sqlite3` | SQLite data file |
| `SEEKDB_PATH` / `SEEKDB_HOST` / `SEEKDB_PORT` | — | Optional seekdb settings; server mode uses `STORAGE_BACKEND=oceanbase` |
| `DEFAULT_SCOPE` | `me/work` | Scope used when `--scope` is omitted |
| `EVOLUTION_ENABLED` | `true` | Whether auto-evolution runs |
| `LIFECYCLE_INTERVAL_SECONDS` | `3600` | Daemon auto-evolution period (seconds) |
| `WATCH_PATHS` | — | `~/notes:me/work,~/docs:me/research` list of `dir:scope` pairs |
| `EMBEDDING_*` | — | Optional; enables vector retrieval when configured |
| `LLM_*` | — | Optional; improves evolution quality (works without an LLM) |
| `SKILL_EXPORT_ENABLED` / `_DIR` / `_MIN_CONFIDENCE` | `false` / `~/.contextseek/skills` / `0.8` | Daemon materializes skills as `SKILL.md` after each evolution cycle |

Full list in the [settings reference](../reference/settings.md).

> ⚠️ Do not point `WATCH_PATHS` at `SKILL_EXPORT_DIR`, or exported skills get re-ingested in a loop.

---

## Scope resolution

`--scope` is optional for most commands: when omitted it falls back to `DEFAULT_SCOPE` in `config.env`; if neither is set the command exits with an error.

For personal use, `me/<area>` naming is recommended (`me/work`, `me/research`); team / multi-tenant scenarios keep the `team/project/user` three-segment form. Use consistent scope naming across all paths (CLI / SDK / HTTP / MCP).

---

## The client-side trio

### `init` — one-time setup

```bash
contextseek init
```

Behavior:

1. Creates the `~/.contextseek/` tree and `config.env` / `mcp.json` templates (never overwrites existing files).
2. If Claude Desktop config is detected, interactively offers to merge the contextseek MCP server (backs up the original first).
3. Registers the background service:
   - **Linux** → writes `~/.config/systemd/user/contextseek.service` and runs `systemctl --user enable --now`
   - **macOS** → writes `~/Library/LaunchAgents/com.contextseek.daemon.plist` and runs `launchctl load`
   - Other platforms → prints a hint to run `contextseek daemon start` manually

`init` does not open the storage backend, so it is safe to run before the seekdb data file exists.

### `daemon` — the background process

The daemon combines three long-running components in one process:

- **LifecycleScheduler** — periodic compact + dream for registered scopes, every `LIFECYCLE_INTERVAL_SECONDS`
- **FileWatcher** — incremental sync when `WATCH_PATHS` change (needs the `daemon` extra)
- **MCP HTTP server** — exposes contextseek tools on `127.0.0.1:2882` (needs the `http` extra)

```bash
contextseek daemon start            # background start (auto fork + detach)
contextseek daemon start --foreground   # foreground, for systemd/launchd
contextseek daemon start --config-dir /path/to/dir
contextseek daemon status           # running state / PID / uptime / components / last-7-day evolution stats
contextseek daemon stop             # graceful SIGTERM
contextseek daemon restart          # stop + start
```

`daemon status` reads `daemon.pid`, `daemon.status.json`, and `logs/lifecycle.jsonl`, works cross-process, and aggregates `evolved` / `merged` counts over the last 7 days.

### `sync` — import notes / docs / chat exports

```bash
contextseek sync <path> [--scope me/work] [--dry-run]
```

Auto-detects the source format — no `--from` flag:

| Format | Trigger |
|--------|---------|
| Directory recursion | A directory (skips `.git`/`node_modules`/`.venv` etc.) |
| Markdown / text | `.md` / `.txt` (strips YAML front-matter, normalizes `[[wikilinks]]`) |
| Code | `.py` via AST chunking; other languages split on blank lines |
| ChatGPT export | `.json` with a `mapping` field (assistant messages) |
| Claude export | `.json` with `conversations` / a list |
| Browser bookmarks | `bookmarks.html` (Netscape format) |
| Plain text | Any other UTF-8-decodable file |

Features:

- **Content-hash dedup** — repeated runs never re-import;
- **mtime fast path** (seekdb backend) — unchanged files skip parsing and hashing;
- `--dry-run` — detect format and count "would import / already exist" without writing.

`sync` skips write-time conflict checks for bulk-import speed; run `contextseek lint` afterward for merge / contradiction review.

---

## Command reference

> Convention: `retrieve` / `overview` / `lint` print human-readable rich text by default (add `--json` for machine output), and `sync` / `skill-export` print rich panels. Server/process commands such as `daemon` and `desktop-server` print status logs; most other data commands print **JSON** to stdout.

### Write & retrieve

| Command | Key args | Description |
|---------|----------|-------------|
| `add` | `--content`(req) `--source` `--tags` | Write a context item, returns `{id, stage}` |
| `retrieve` | `--query`(req) `--k`(10) `--full` `--json` `--tags` | Ranked SearchHits; L1 summaries by default, `--full` for L0. `--tags a,b` requires returned items to carry all listed tags |
| `expand` | `--ids`(req, comma-separated) | Expand retrieved ids to L0 full content |
| `items` | `--stage`(raw/extracted/knowledge/skill) | List all items in a scope |

```bash
contextseek add --scope me/work --content "Prefer concise answers" --source cli --tags preference,language
contextseek retrieve --scope me/work --query "language preference" --k 5
contextseek retrieve --scope me/work --query "language preference" --tags preference,language
contextseek retrieve --scope me/work --query "language preference" --k 3 --full
contextseek expand --scope me/work --ids 1a2b3c,4d5e6f
contextseek items --scope me/work --stage knowledge
```

### Evolution & maintenance

| Command | Key args | Description |
|---------|----------|-------------|
| `compact` | `--dry-run` | Run evolution: merge / archive / advance, returns counts |
| `dream` | `--dry-run` | Dream cycle: pattern consolidation + cross-cluster divergence |
| `overview` | `--json` | Scope summary: stage distribution, growth, skills, health score |
| `lint` | `--fix` `--show ID1 ID2` `--json` | KB health check: orphans, contradictions, distillation gaps |
| `feedback` | `--item-id`(req) `--score`(req) `--reason` | Apply relevance feedback to an item (-1.0–1.0) |

```bash
contextseek compact --scope me/work --dry-run
contextseek dream --scope me/work
contextseek overview --scope me/work
contextseek lint --scope me/work
contextseek lint --scope me/work --fix                 # auto-archive orphans
contextseek lint --scope me/work --show 1a2b3c 4d5e6f  # side-by-side (contradiction review)
contextseek feedback --scope me/work --item-id 1a2b3c --score 0.5 --reason useful
```

### Provenance & deletion

| Command | Key args | Description |
|---------|----------|-------------|
| `forget` | `--item-id`(req) `--reason` | Soft-delete (mark, not physical removal) |
| `delete` | `--item-id`(req) `--reason` `--no-propagate` | Hard-delete; propagates invalidation to dependents by default |
| `upstream` | `--item-id`(req) | Walk `derived_from`/`supported_by` to find sources |
| `evidence-chain` | `--item-id`(req) `--max-depth`(10) | Compute the full evidence-chain DAG |
| `chain-confidence` | `--item-id`(req) | Quick propagated-confidence lookup |

```bash
contextseek forget --scope me/work --item-id 1a2b3c --reason outdated
contextseek delete --scope me/work --item-id 1a2b3c --no-propagate
contextseek upstream --scope me/work --item-id 1a2b3c
contextseek evidence-chain --scope me/work --item-id 1a2b3c --max-depth 5
contextseek chain-confidence --scope me/work --item-id 1a2b3c
```

`--item-id` accepts both a short id and a full `contextseek://...` ref.

### Skills & tools

| Command | Key args | Description |
|---------|----------|-------------|
| `tools` | `--format`(openai/anthropic) | Print the built-in retrieve/expand LLM tool spec |
| `skill-tools` | `--fmt`(openai/anthropic/mcp) `--query` `--k`(20) | Export tool/mcp skills as LLM tool definitions |
| `skill-context` | `--query` `--k`(5) | Render prompt skills as a system prompt block |
| `skill-import` | `--format`(hermes/openai/mcp)(req) `--path`(req) | Import skills from Hermes dir / OpenAI / MCP JSON |
| `skill-export` | `--out` `--min-confidence`(0.8) `--dry-run` `--no-prune` | Materialize prompt skills as `SKILL.md` for agent tools |

```bash
contextseek tools --format anthropic
contextseek skill-tools --scope me/work --fmt mcp --query "database"
contextseek skill-context --scope me/work --query "review convention"
contextseek skill-import --scope me/work --format openai --path ./functions.json
contextseek skill-export --scope me/work --out ~/.contextseek/skills --dry-run
```

### Ops

| Command | Key args | Description |
|---------|----------|-------------|
| `metrics` | — | Print Prometheus-format metrics |
| `desktop-server` | `--host` `--port` `--data-dir` `--log-level` | Run the same-origin backend for the desktop app: HTTP API plus the built dashboard SPA |

```bash
contextseek metrics
contextseek desktop-server --host 127.0.0.1 --port 8000
```

---

## Output formats

- **JSON-by-default** commands suit pipes and scripts:
  ```bash
  contextseek retrieve --scope me/work --query db --json | jq '.items[].id'
  contextseek items --scope me/work | jq '.items | length'
  ```
- `retrieve` / `overview` / `lint` default to human-readable output (rich text + health bar + next-step hints); add `--json` for machine format.

---

## Calling the CLI from code

The CLI entry point is a plain function, reusable in tests or scripts, and accepts an injected client:

```python
from contextseek.cli.main import run_cli

run_cli(["retrieve", "--scope", "me/work", "--query", "test", "--k", "5"])

# Inject a pre-built client (bypass settings-based init)
from contextseek import ContextSeek
ctx = ContextSeek.from_settings()
run_cli(["overview", "--scope", "me/work"], client=ctx)
```

The return value is the process exit code (`0` = success).

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `--scope is required` | Command omits `--scope` and `config.env` has no `DEFAULT_SCOPE` |
| `daemon failed to start` | See `~/.contextseek/logs/`; confirm `http` extra (MCP server) and `daemon` extra (file watching) are installed |
| `sync` watching has no effect | `WATCH_PATHS` must be set in `config.env` and needs `contextseek[daemon]` (watchdog) |
| Empty retrieval | Confirm matching scope, that you ran `sync`/`add`, and the embedding model loaded |
| MCP tools won't connect | The daemon's MCP server defaults to `127.0.0.1:2882`; or use `contextseek-mcp-stdio` |

More in the [troubleshooting guide](../troubleshooting.md).

---

## Related

- [MCP / HTTP / CLI](integrations/mcp-http-cli.md) — four-path comparison, full MCP tool table, selection guide
- [Write & retrieve](write-and-retrieve.md) — pipeline, filters, agent loop
- [Evolution](evolution.md) — compact / dream / feedback / overview / skill
- [Settings reference](../reference/settings.md) — all environment variables
- [Configuration](../getting-started/configuration.md) — config files and phased rollout
