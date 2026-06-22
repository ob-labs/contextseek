# 安装

ContextSeek 需要 **Python 3.11、3.12 或 3.13**。核心 wheel 包含 SDK、CLI 与 MCP 入口；默认 SQLite 后端**不需要**任何 API Key。

## 系统要求

| 组件 | 要求 |
|------|------|
| Python | ≥ 3.11 |
| 操作系统 | Linux、macOS、Windows（生产环境建议 WSL） |
| 磁盘 | 默认 SQLite 使用 `SQLITE_PATH`；`file` 需规划 `STORAGE_PATH` 容量 |
| 网络 | 仅在使用远程 Embedding/LLM 或 OceanBase 时需要 |

## 从 PyPI 安装

```bash
python3 --version
pip install -U pip
pip install contextseek
```

验证：

```bash
contextseek --help
python -c "from contextseek import ContextSeek; print(ContextSeek.from_settings())"
```

安装的命令：

| 命令 | 作用 |
|------|------|
| `contextseek` | CLI |
| `contextseek-mcp-stdio` | MCP stdio |
| `contextseek-mcp-sse` | MCP SSE（可 `--port`） |

## 可选 extras（说明）

| Extra | 主要依赖 | 安装 |
|-------|----------|------|
| 核心 | seekvfs、pydantic 等 | `pip install contextseek` |
| `http` | FastAPI、Uvicorn | `pip install contextseek[http]` |
| `langchain` | langchain-core | `pip install contextseek[langchain]` |
| `openai` | langchain-openai | `pip install contextseek[langchain,openai]` |
| `ollama` | langchain-ollama | `pip install contextseek[langchain,ollama]` |
| `huggingface` | langchain-huggingface | `pip install contextseek[langchain,huggingface]` |
| `dashscope` | langchain-community、DashScope SDK | `pip install contextseek[langchain,dashscope]` |
| `oceanbase` | pyobvector、SQLAlchemy | `pip install contextseek[oceanbase]` |
| `test` | pytest | `pip install contextseek[test]` |

### 推荐组合

```bash
pip install "contextseek[http,langchain,openai]"
pip install "contextseek[oceanbase,langchain,openai,http]"
pip install "contextseek[oceanbase,langchain,dashscope,http]"
pip install -e ".[test]"   # 贡献代码
```

> **注意：** 仅装 `langchain` 不会安装具体 Embedding/Chat 实现。将 `EMBEDDING_PROVIDER` / `LLM_PROVIDER` 设为 `openai`、`ollama`、`huggingface` 或 `dashscope` 时，需要安装对应 extra 或手动安装 provider 包。

## 从源码安装

### uv（贡献者推荐）

```bash
git clone https://github.com/ob-labs/contextseek.git
cd contextseek
uv sync
source .venv/bin/activate
uv run pytest tests/ -q
```

### pip 可编辑安装

```bash
git clone https://github.com/ob-labs/contextseek.git
cd contextseek
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest tests/ -q
```

运行示例：

```bash
uv run python examples/full_pipeline_file.py
```

## 依赖 seekvfs

自动安装 **[seekvfs](https://github.com/oceanbase/seekvfs)**，一般无需单独配置，除非自定义存储适配器。

## 虚拟环境与 Docker

**venv / conda：** 运行 CLI 或示例前务必 `activate` 对应环境。

**Docker 示例：**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install "contextseek[http,langchain,openai]"
ENV STORAGE_BACKEND=file
ENV STORAGE_PATH=/data/contextseek
CMD ["uvicorn", "contextseek.http.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

持久化请挂载 `/data/contextseek`；`OPENAI_API_KEY` 在运行时注入，不要写入镜像层。

## 安装问题排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 找不到 `contextseek` 命令 | PATH 未含 venv/bin | 激活 venv 或用 `python -m` |
| `No module named contextseek` | 解释器不对 | 检查 `which python` |
| 缺少 `langchain_openai` | 未装 openai extra | `pip install contextseek[openai]` |
| 测试报 OpenAI 鉴权 | 本地 `.env` 启用了 LLM | 临时移走 `.env` 或设 `LLM_PROVIDER=none` |
| OceanBase 导入失败 | 未装 oceanbase extra | `pip install contextseek[oceanbase]` |

## 安全提示

勿将 API Key 写入镜像或提交 `.env`；生产环境用密钥管理服务。

## 下一步

1. [快速上手](quickstart.md)  
2. [配置](configuration.md)  
3. [核心概念](../guides/core-concepts.md)  
4. [写入与检索](../guides/write-and-retrieve.md)  
