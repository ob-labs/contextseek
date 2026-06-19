# CLI 命令行（端侧 / 个人模式）

`contextseek` 命令行是 ContextSeek 的端侧入口：在本机用嵌入式 `seekdb` 跑一个零依赖的个人知识库，配合后台 `daemon` 自动演进、自动同步笔记，并把同一套能力通过 MCP 暴露给 Claude Desktop、Cursor 等工具。

所有命令底层都是同一个 `ContextSeek` 客户端逻辑；CLI、SDK、HTTP、MCP 四条通路完全等价（见 [MCP / HTTP / CLI](integrations/mcp-http-cli.md)）。

---

## 安装

```bash
# 端侧推荐：嵌入式 seekdb（无需外部服务）
pip install "contextseek[seekdb]"

# 如需后台文件监听（daemon 的 WATCH_PATHS 自动同步）
pip install "contextseek[seekdb,daemon]"

# 一次装齐（含 HTTP / MCP / LangChain / seekdb / watchdog）
pip install "contextseek[all]"
```

安装后会提供三个可执行文件：

| 命令 | 用途 |
|------|------|
| `contextseek` | 主 CLI（本文档） |
| `contextseek-mcp-stdio` | 本地 stdio MCP server |
| `contextseek-mcp-sse` | 远程 SSE MCP server（需 `http` extra） |

---

## 五分钟上手

```bash
# 1. 初始化 ~/.contextseek/（生成配置、注册后台服务）
contextseek init

# 2. 确认后台 daemon 在跑
contextseek daemon status

# 3. 导入已有笔记 / 文档（自动识别格式）
contextseek sync ~/notes --scope me/work

# 4. 写入一条上下文
contextseek add --scope me/work --content "团队代码评审统一用中文" --tags convention

# 5. 检索（默认返回 L1 摘要）
contextseek retrieve --scope me/work --query "代码评审规范"

# 6. 看看 scope 全貌
contextseek overview --scope me/work
```

> 第一次 `add` / `retrieve` 会触发内置 embedding 模型（`all-MiniLM-L6-v2`，ONNX）下载与加载，属正常现象。

---

## `~/.contextseek/` 目录

`contextseek init` 会创建并初始化端侧工作目录：

```
~/.contextseek/
├── config.env            # 端侧配置（等价于项目里的 .env）
├── mcp.json              # 可粘贴进 AI 工具的 MCP 配置片段
├── seekdb.db             # 嵌入式 seekdb 数据文件
├── daemon.pid            # 后台进程 PID
├── daemon.status.json    # 后台组件状态
├── logs/
│   └── lifecycle.jsonl   # 演进 / 同步生命周期日志
├── backups/              # 演进前自动快照
└── skills/               # （可选）导出的 SKILL.md
```

配置文件位置可用环境变量 `CONTEXTSEEK_CONFIG` 覆盖（systemd / launchd 服务即通过它指向 `config.env`）。

### config.env 关键项

| 键 | 默认 | 说明 |
|----|------|------|
| `STORAGE_BACKEND` | `seekdb` | 嵌入式模式填写 `seekdb`；seekdb server mode 填写 `oceanbase` |
| `SEEKDB_PATH` | `~/.contextseek/seekdb.db` | 嵌入式数据文件 |
| `SEEKDB_HOST` / `SEEKDB_PORT` | — | 取消注释切换到 seekdb server 模式；仅 server mode 需要使用 `STORAGE_BACKEND=oceanbase` |
| `DEFAULT_SCOPE` | `me/work` | 省略 `--scope` 时使用的默认 scope |
| `EVOLUTION_ENABLED` | `true` | 是否开启自动演进 |
| `LIFECYCLE_INTERVAL_SECONDS` | `3600` | daemon 自动演进周期（秒） |
| `WATCH_PATHS` | — | `~/notes:me/work,~/docs:me/research` 形式的「目录:scope」列表 |
| `EMBEDDING_*` | 内置 ONNX | 可切 OpenAI 等 embedding |
| `LLM_*` | — | 可选；配置后演进质量更高（无 LLM 也能跑） |
| `SKILL_EXPORT_ENABLED` / `_DIR` / `_MIN_CONFIDENCE` | `false` / `~/.contextseek/skills` / `0.8` | daemon 每轮演进后把 skill 物化为 `SKILL.md` |

完整变量见 [配置项参考](../reference/settings.md)。

> ⚠️ 不要把 `WATCH_PATHS` 指向 `SKILL_EXPORT_DIR`，否则导出的 skill 会被再次摄入，形成回环。

---

## Scope 解析

`--scope` 对绝大多数命令是可选的：省略时回落到 `config.env` 里的 `DEFAULT_SCOPE`；两者都没有则报错退出。

端侧个人用法推荐 `me/<领域>` 命名（如 `me/work`、`me/research`）；团队 / 多租户场景沿用 `team/project/user` 三段式。各通路（CLI / SDK / HTTP / MCP）务必使用一致的 scope 命名。

---

## 端侧三件套

### `init` — 一次性初始化

```bash
contextseek init
```

行为：

1. 创建 `~/.contextseek/` 目录树与 `config.env`、`mcp.json` 模板（已存在则不覆盖）。
2. 检测到 Claude Desktop 配置时，交互式询问是否合并 contextseek MCP server（会先备份原配置）。
3. 注册后台服务：
   - **Linux** → 写入 `~/.config/systemd/user/contextseek.service` 并 `systemctl --user enable --now`
   - **macOS** → 写入 `~/Library/LaunchAgents/com.contextseek.daemon.plist` 并 `launchctl load`
   - 其他平台 → 提示手动 `contextseek daemon start`

`init` 不会打开存储后端，因此可在 seekdb 数据文件存在之前安全运行。

### `daemon` — 后台进程

后台 daemon 在一个进程内合并三个长驻组件：

- **LifecycleScheduler** — 按 `LIFECYCLE_INTERVAL_SECONDS` 周期对已注册 scope 执行 compact + dream
- **FileWatcher** — 监听 `WATCH_PATHS`，文件变更时增量 sync（需 `daemon` extra）
- **MCP HTTP server** — 在 `127.0.0.1:2882` 暴露 contextseek 工具（需 `http` extra）

```bash
contextseek daemon start            # 后台启动（自动 fork + detach）
contextseek daemon start --foreground   # 前台运行，供 systemd/launchd 调用
contextseek daemon start --config-dir /path/to/dir
contextseek daemon status           # 查看运行状态 / PID / uptime / 组件 / 近 7 天演进统计
contextseek daemon stop             # SIGTERM 优雅停止
contextseek daemon restart          # stop + start
```

`daemon status` 读取 `daemon.pid`、`daemon.status.json` 与 `logs/lifecycle.jsonl`，可跨进程查看，并汇总最近 7 天的 `evolved` / `merged` 数量。

### `sync` — 导入笔记 / 文档 / 对话导出

```bash
contextseek sync <路径> [--scope me/work] [--dry-run]
```

自动识别来源格式，无需 `--from` 标志：

| 格式 | 触发条件 |
|------|----------|
| 目录递归 | 传入目录（跳过 `.git`/`node_modules`/`.venv` 等） |
| Markdown / 文本 | `.md` / `.txt`（剥离 YAML front-matter，归一化 `[[wikilink]]`） |
| 代码 | `.py` 走 AST 切块；其它语言按空行切块 |
| ChatGPT 导出 | 含 `mapping` 字段的 `.json`（取 assistant 消息） |
| Claude 导出 | 含 `conversations` 的 `.json` / 列表 |
| 浏览器书签 | `bookmarks.html`（Netscape 格式） |
| 纯文本 | 其它可解码为 UTF-8 的文件 |

特性：

- **内容哈希去重** — 重复运行不会重复导入；
- **mtime 快路径**（seekdb 后端）— 未变更文件直接跳过解析与哈希；
- `--dry-run` — 仅检测格式并统计「会导入多少 / 已存在多少」，不写入。

sync 会跳过写入时的冲突检测以保证批量导入速度；导入后用 `contextseek lint` 做合并 / 矛盾审查。

---

## 命令全表

> 约定：除 `retrieve` / `overview` / `lint` 默认输出人类可读的富文本（可加 `--json` 切机器格式）、`sync` / `skill-export` 输出富文本面板外，其余命令均向 stdout 打印 **JSON**。

### 写入与检索

| 命令 | 关键参数 | 说明 |
|------|----------|------|
| `add` | `--content`(必填) `--source` `--tags` | 写入一条上下文，返回 `{id, stage}` |
| `retrieve` | `--query`(必填) `--k`(10) `--full` `--json` `--tags` | 检索排序后的 SearchHit；默认 L1 摘要，`--full` 返回 L0 全文。`--tags a,b` 要求返回项同时包含列出的所有标签 |
| `expand` | `--ids`(必填，逗号分隔) | 把已检索 id 升档到 L0 全文 |
| `items` | `--stage`(raw/extracted/knowledge/skill) | 列举 scope 内全部 item |

```bash
contextseek add --scope me/work --content "偏好简洁回答" --source cli --tags preference,language
contextseek retrieve --scope me/work --query "语言偏好" --k 5
contextseek retrieve --scope me/work --query "语言偏好" --tags preference,language
contextseek retrieve --scope me/work --query "语言偏好" --k 3 --full
contextseek expand --scope me/work --ids 1a2b3c,4d5e6f
contextseek items --scope me/work --stage knowledge
```

### 演进与维护

| 命令 | 关键参数 | 说明 |
|------|----------|------|
| `compact` | `--dry-run` | 触发演进：合并 / 归档 / 升档，返回各计数 |
| `dream` | `--dry-run` | Dream 周期：模式巩固 + 跨簇发散假设 |
| `overview` | `--json` | scope 全貌：stage 分布、成长进度、skill、健康分 |
| `lint` | `--fix` `--show ID1 ID2` `--json` | 知识库体检：孤儿项、矛盾、蒸馏机会，给出健康分 |
| `feedback` | `--item-id`(必填) `--score`(必填) `--reason` | 对某项施加相关性反馈（-1.0~1.0） |

```bash
contextseek compact --scope me/work --dry-run
contextseek dream --scope me/work
contextseek overview --scope me/work
contextseek lint --scope me/work
contextseek lint --scope me/work --fix                 # 自动归档孤儿项
contextseek lint --scope me/work --show 1a2b3c 4d5e6f  # 并排对比两项（审查矛盾）
contextseek feedback --scope me/work --item-id 1a2b3c --score 0.5 --reason useful
```

### 溯源与删除

| 命令 | 关键参数 | 说明 |
|------|----------|------|
| `forget` | `--item-id`(必填) `--reason` | 软删除（标记，不物理删除） |
| `delete` | `--item-id`(必填) `--reason` `--no-propagate` | 硬删除；默认向依赖项传播失效 |
| `upstream` | `--item-id`(必填) | 沿 `derived_from`/`supported_by` 上溯来源 |
| `evidence-chain` | `--item-id`(必填) `--max-depth`(10) | 计算完整证据链 DAG |
| `chain-confidence` | `--item-id`(必填) | 快速查询传播后的置信度 |

```bash
contextseek forget --scope me/work --item-id 1a2b3c --reason outdated
contextseek delete --scope me/work --item-id 1a2b3c --no-propagate
contextseek upstream --scope me/work --item-id 1a2b3c
contextseek evidence-chain --scope me/work --item-id 1a2b3c --max-depth 5
contextseek chain-confidence --scope me/work --item-id 1a2b3c
```

`--item-id` 既接受短 id，也接受完整的 `contextseek://...` ref。

### Skill 与工具

| 命令 | 关键参数 | 说明 |
|------|----------|------|
| `tools` | `--format`(openai/anthropic) | 打印内置 retrieve/expand 的 LLM 工具描述 |
| `skill-tools` | `--fmt`(openai/anthropic/mcp) `--query` `--k`(20) | 把 tool/mcp 类 skill 导出为 LLM 工具定义 |
| `skill-context` | `--query` `--k`(5) | 把 prompt 类 skill 渲染成 system prompt 块 |
| `skill-import` | `--format`(hermes/openai/mcp)(必填) `--path`(必填) | 从 Hermes 目录 / OpenAI / MCP JSON 导入 skill |
| `skill-export` | `--out` `--min-confidence`(0.8) `--dry-run` `--no-prune` | 把 prompt skill 物化为 `SKILL.md` 供 agent 工具读取 |

```bash
contextseek tools --format anthropic
contextseek skill-tools --scope me/work --fmt mcp --query "数据库"
contextseek skill-context --scope me/work --query "评审规范"
contextseek skill-import --scope me/work --format openai --path ./functions.json
contextseek skill-export --scope me/work --out ~/.contextseek/skills --dry-run
```

### 运维

| 命令 | 说明 |
|------|------|
| `metrics` | 打印 Prometheus 文本格式指标 |

```bash
contextseek metrics
```

---

## 输出格式

- **默认 JSON** 的命令适合管道与脚本：
  ```bash
  contextseek retrieve --scope me/work --query db --json | jq '.items[].id'
  contextseek items --scope me/work | jq '.items | length'
  ```
- `retrieve` / `overview` / `lint` 默认给人看（富文本 + 健康条 + 后续建议），加 `--json` 切机器格式。

---

## 在代码里调用 CLI

CLI 入口是纯函数，可在测试或脚本中直接复用，支持注入自定义 client：

```python
from contextseek.cli.main import run_cli

run_cli(["retrieve", "--scope", "me/work", "--query", "test", "--k", "5"])

# 注入已构造的 client（绕过从 settings 初始化）
from contextseek import ContextSeek
ctx = ContextSeek.from_settings()
run_cli(["overview", "--scope", "me/work"], client=ctx)
```

返回值即进程退出码（`0` 成功）。

---

## 故障排查

| 现象 | 排查 |
|------|------|
| `--scope is required` | 命令未带 `--scope` 且 `config.env` 未设 `DEFAULT_SCOPE` |
| `daemon failed to start` | 看 `~/.contextseek/logs/`；确认已装 `http` extra（MCP server）与 `daemon` extra（文件监听） |
| `sync` 监听不生效 | `WATCH_PATHS` 需在 `config.env` 配置，且需 `contextseek[daemon]`（watchdog） |
| 检索结果为空 | 确认 scope 一致、已 `sync`/`add`、embedding 模型已加载 |
| MCP 工具连不上 | daemon 的 MCP server 默认 `127.0.0.1:2882`；或用 `contextseek-mcp-stdio` |

更多见 [故障排查指南](../troubleshooting.md)。

---

## 相关

- [MCP / HTTP / CLI](integrations/mcp-http-cli.md) — 四通路对照、MCP 工具全表、选型
- [写入与检索](write-and-retrieve.md) — 管线、过滤、Agent 闭环
- [上下文演进](evolution.md) — compact / dream / feedback / overview / skill
- [配置项参考](../reference/settings.md) — 全部环境变量
- [配置](../getting-started/configuration.md) — 配置档与分阶段上线
