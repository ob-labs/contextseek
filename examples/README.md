# Examples

示例按依赖复杂度分为三组，建议按顺序探索。

## [basic/](basic/) — 入门示例

依赖最小，适合初次了解 ContextSeek。

| 文件 | 后端 | 说明 |
|---|---|---|
| `pipeline_file.py` | FileBackend（无需外部服务） | 本地文件后端，关键词检索 |
| `pipeline_ob.py` | OceanBase | 向量 + 全文混合检索 |
| `langchain.py` | FileBackend | LangChain Memory / Retriever 桥接 |

```bash
uv run python examples/basic/pipeline_file.py  # 零外部依赖，推荐首选
```

## [advanced/](advanced/) — 完整能力展示

涵盖 LLM 集成、演进流水线、DataPlug 扩展。

| 文件 | 依赖 | 说明 |
|---|---|---|
| `research_agent.py` | 仅项目本身 | 所有核心功能综合演示（推荐） |
| `evidence_chain.py` | 仅项目本身 | 证据链溯源：`upstream` / `evidence_chain` / `chain_confidence` |
| `llm_full_pipeline_ob.py` | OB + LLM API | Phase 1/2/3 完整 LLM 流水线 |
| `powermem_minimal.py` | 仅项目本身 | PowerMem 最小集成路径（~50 行） |
| `powermem_plug.py` | 可选 powermem | PowerMem DataPlug 完整演示 |

```bash
uv run python examples/advanced/research_agent.py  # 推荐：零外部依赖的完整演示
```

## [gis/](gis/) — 地理空间场景

需要 OceanBase >= 4.2.2 且 `GEO_ENABLED=true`。

| 文件 | 场景 |
|---|---|
| `poi_search.py` | 地图 POI 关键词 + 地理混合搜索 |
| `ride_hailing.py` | 打车调度：司机 / 订单 / 热力区域 |
| `autonomous_driving.py` | 智能驾驶：HD 地图 / ODD / 道路事件 |

```bash
GEO_ENABLED=true uv run python examples/gis/poi_search.py
```

---

## HTTP API

启动 API 服务：

```bash
uvicorn contextseek.http.server:app --host 127.0.0.1 --port 8000 --reload
```

示例请求：

```bash
curl -X POST http://127.0.0.1:8000/add \
  -H "Content-Type: application/json" \
  -d '{"content": "hello", "scope": "t/p/u", "source": "curl"}'

curl -X POST http://127.0.0.1:8000/retrieve \
  -H "Content-Type: application/json" \
  -d '{"query": "hello", "scope": "t/p/u", "k": 5}'
```

端点：`/add`、`/retrieve`、`/expand`、`/compact`、`/forget`、`/delete`、`/health`
