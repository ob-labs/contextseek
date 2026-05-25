# 示例索引

所有示例脚本位于 [`examples/`](../../../examples/)。除特别说明外，使用 `uv run python examples/<脚本>.py` 运行。

---

## 脚本一览

| 脚本 | 用途 | 所需 extras |
|---|---|---|
| [`full_pipeline_file.py`](../../../examples/full_pipeline_file.py) | FileBackend 全流程：add、retrieve、expand、RetrievalOrchestrator | *(无)* |
| [`full_pipeline_ob.py`](../../../examples/full_pipeline_ob.py) | OceanBase + LangChain Embedder：向量 + 全文混合召回 | `oceanbase`、`langchain`、`openai` |
| [`langchain_pipeline.py`](../../../examples/langchain_pipeline.py) | `ContextSeekMemory` 与 `ContextSeekRetriever` 桥接适配器 | `langchain`、`openai` |
| [`research_agent_demo.py`](../../../examples/research_agent_demo.py) | 综合演示：全部核心功能，零外部依赖 | *(无)* |
| [`evidence_chain.py`](../../../examples/advanced/evidence_chain.py) | 证据链溯源：`upstream` / `evidence_chain` / `chain_confidence` | *(无)* |
| [`powermem_minimal.py`](../../../examples/powermem_minimal.py) | PowerMem → ContextSeek 最小集成示例 | *(无)* |
| [`powermem_plug_demo.py`](../../../examples/powermem_plug_demo.py) | `PowerMemPlug` 完整演练：混合来源统一 `retrieve()` | *(无；live 模式需 `pip install powermem`)* |
| [`llm_full_pipeline_oceanbase.py`](../../../examples/llm_full_pipeline_oceanbase.py) | 真实 LLM + OceanBase：全部 `EVOLUTION_LLM_*` 功能端到端演示 | `oceanbase`、`langchain`、`openai` |

---

## 功能覆盖对应表

| 功能 | 示例脚本 |
|---|---|
| InMemory / File 后端 | `full_pipeline_file.py`、`research_agent_demo.py` |
| OceanBase + 混合检索 | `full_pipeline_ob.py`、`llm_full_pipeline_oceanbase.py` |
| LangChain 桥接 | `langchain_pipeline.py` |
| DataPlug（RAG、记忆、轨迹）| `powermem_minimal.py`、`powermem_plug_demo.py` |
| 演化（`compact`、`dream`、`feedback`）| `research_agent_demo.py`、`llm_full_pipeline_oceanbase.py` |
| 证据链 + 溯源 | `evidence_chain.py` |
| `skill_tools()` / `skill_context()` | `research_agent_demo.py` |
| 策略路由 + 金丝雀 | `research_agent_demo.py` |
| LLM 重排序 + 摘要 | `llm_full_pipeline_oceanbase.py` |
| 轨迹导出 | `research_agent_demo.py` |

---

详细运行说明和预期输出见 [`examples/README.md`](../../../examples/README.md)。
