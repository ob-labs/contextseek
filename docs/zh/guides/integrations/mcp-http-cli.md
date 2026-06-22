# MCP、HTTP 与 CLI

ContextSeek 通过四种方式暴露同一套能力：

| 方式 | 适用 |
|------|------|
| **Python SDK** | 进程内 Agent / 服务 |
| **CLI** | 脚本、cron、本地调试 |
| **HTTP** | 多语言、网关后微服务 |
| **MCP** | Cursor、Claude Desktop 等工具宿主 |

底层均为同一 `ContextSeek` 客户端逻辑。

---

## CLI（`contextseek`）

```bash
pip install contextseek
contextseek --help
```

读取 cwd 或仓库根目录 `.env`（见 [配置](../../getting-started/configuration.md)）。

> 端侧 / 个人模式（`init`、`daemon`、`sync`、默认 SQLite）与命令全表见 **[CLI 命令行（端侧）](../cli.md)**。下面只列常用速查。

### 命令一览

| 命令 | 说明 |
|------|------|
| `add` | 写入 |
| `retrieve` | 检索（`--k`、`--full`） |
| `expand` | 按 `--ids` 升档 L0 |
| `compact` | 演进（`--dry-run`） |
| `forget` / `delete` | 软删 / 硬删 |
| `overview` | Scope 汇总 |
| `dream` | Dream 周期 |
| `feedback` | 打分 |
| `upstream` / `evidence-chain` / `chain-confidence` | 溯源 |
| `tools` | 内置 retrieve/expand 工具描述 |
| `skill-tools` / `skill-context` / `skill-import` | Skill 相关 |
| `items` | 列举（`--stage`） |
| `metrics` | Prometheus 文本 |

### 示例

```bash
contextseek add --scope acme/proj/user --content "偏好中文简洁回答" --source cli --tags preference,language

contextseek retrieve --scope acme/proj/user --query "语言偏好" --k 5
contextseek retrieve --scope acme/proj/user --query "语言偏好" --k 3 --full

contextseek expand --scope acme/proj/user --ids id1,id2
contextseek overview --scope acme/proj/user
contextseek compact --scope acme/proj/user
contextseek forget --scope acme/proj/user --item-id abc123 --reason cleanup
```

标准输出为 **JSON**。

### 代码调用 CLI

```python
from contextseek.cli.main import run_cli
run_cli(["retrieve", "--scope", "t/p/u", "--query", "test", "--k", "5"])
```

---

## HTTP API（FastAPI）

```bash
pip install "contextseek[http]"
uvicorn contextseek.http.server:app --host 0.0.0.0 --port 8000
```

文档：`http://localhost:8000/docs`

### 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/add` | 写入 |
| `POST` | `/retrieve` | `query`,`k`,`full`,`filters` |
| `POST` | `/expand` | `ids[]` |
| `POST` | `/forget` `/delete` | 删除 |
| `POST` | `/compact` `/dream` | 维护 |
| `POST` | `/feedback` | 反馈 |
| `POST` | `/upstream` `/evidence_chain` `/chain_confidence` | 溯源 |
| `POST` | `/skill_tools` `/skill_context` | Skill |
| `POST` | `/items` | 列举 |
| `GET` | `/overview?scope=` | 汇总 |
| `GET` | `/metrics` `/health` | 指标；健康检查含 `version`（与 OpenAPI `info.version` 一致） |

### curl

```bash
curl -s -X POST http://localhost:8000/add \
  -H "Content-Type: application/json" \
  -d '{"scope":"t/p/u","content":"hello","source":"curl"}'

curl -s -X POST http://localhost:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"scope":"t/p/u","query":"hello","k":5}'

curl -s "http://localhost:8000/overview?scope=t/p/u"
```

### 嵌入自有应用

```python
from contextseek.http.server import create_app
app = create_app(client=ctx)
```

---

## MCP 服务

向支持 JSON-RPC 的宿主暴露工具（如 Cursor）。

```bash
pip install contextseek
contextseek-mcp-stdio          # 本地 stdio
contextseek-mcp-sse --port 8001   # 远程 SSE，需 http extra
```

### Cursor 配置示例

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

### MCP 工具

| 工具 | 等价 SDK |
|------|----------|
| `contextseek_add` | `add` |
| `contextseek_retrieve` | `retrieve` |
| `contextseek_expand` | `expand_by_ids` |
| `contextseek_forget` / `delete` | 删除 |
| `contextseek_compact` / `dream` | 维护 |
| `contextseek_overview` | `overview` |
| `contextseek_feedback` | `feedback` |
| `contextseek_upstream` / `evidence_chain` / `chain_confidence` | 溯源 |
| `contextseek_skill_tools` / `skill_context` | Skill |
| `contextseek_items` | `items` |

### 推荐 Agent 流程

1. `contextseek_retrieve` 拿摘要与 id  
2. 需要细节 → `contextseek_expand`  
3. 可选 `contextseek_feedback`  
4. 长跑 scope 定期 `contextseek_compact`  

### Python 嵌入

```python
from contextseek.mcp.server import ContextSeekMCPServer
from contextseek.mcp.runtime import MCPRuntime

runtime = MCPRuntime(server=ContextSeekMCPServer(client=ctx))
```

---

## 如何选型

| 需求 | 选择 |
|------|------|
| 最低延迟、全 Python | SDK |
| 运维脚本 | CLI |
| 多语言服务 | HTTP |
| IDE 集成 | MCP stdio |
| 远程 MCP | MCP SSE |

各通路应使用一致的 **scope** 命名。

---

## 安全

- 生产环境 HTTP/SSE 需鉴权（网关、mTLS）
- MCP 继承进程环境变量，勿把生产密钥给不可信宿主
- CLI/MCP 遵守 `SECURITY_*` ACL（若开启）

## 相关

- [写入与检索](../write-and-retrieve.md)
- [配置](../../getting-started/configuration.md)
- [可观测性](../observability.md)
