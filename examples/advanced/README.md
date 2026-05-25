# Advanced Examples

完整能力展示，涵盖 LLM 集成、演进流水线和 DataPlug 扩展。

## research_agent.py — 综合功能演示（推荐新用户看完 basic 后从这里开始）

```bash
uv run python examples/advanced/research_agent.py
```

**依赖：** 仅项目本身，无需额外安装。

场景：研究 Agent 调查"分布式数据库"，从原始资料到技能蒸馏的完整链路。

演示：
- 多 `source_type` 的 `ContextItem` 写入与 provenance
- `retrieve(kind=hits)` vs `retrieve(kind=context)`（ranked vs budgeted）
- `expand()` 按需升级 hit 为全量内容
- 条目间链接：`supports` / `refutes` / `supersedes`
- 演进流水线：`raw → extracted → knowledge → skill`
- 生命周期压缩（compaction）
- Trace 写入与训练数据导出
- 策略路由与 canary 规则
- Skill 导出（`skill_tools` / `skill_context`）

---

## evidence_chain.py — 证据链与溯源 API 演示

```bash
uv run python examples/advanced/evidence_chain.py
```

**依赖：** 仅项目本身，无需额外安装。

场景：SRE 故障排查，从监控告警、日志到回滚建议的多层证据 DAG。

演示：
- `derived_from` / `supported_by` / `refuted_by` 链接构建证据图
- `upstream()` — 快速追溯来源
- `evidence_chain()` — 完整 DAG、置信度传播、冲突检测、关键路径
- `chain_confidence()` — 轻量置信度查询

---

## llm_full_pipeline_ob.py — 真实 LLM + OceanBase 完整流水线

```bash
uv run python examples/advanced/llm_full_pipeline_ob.py
```

**依赖：**

```bash
pip install "contextseek[oceanbase,langchain,openai]"
```

演示：
- Phase 1：LLM rerank + dream
- Phase 2：LLM merge + 冲突检测
- Phase 3：LLM stage 推断 + distill + feedback 解析
- 通过 `PromptSettings`（`PROMPT_*` 环境变量）覆盖 prompt 模板

---

## powermem_minimal.py — PowerMem 最小集成路径

```bash
uv run python examples/advanced/powermem_minimal.py
```

**依赖：** 仅项目本身（含内置 mock 数据，无需安装 powermem）。

~50 行演示：已有 PowerMem 用户如何通过 `PowerMemPlug` 接入 ContextSeek，
在不改变 `memory.add` / `memory.search` 习惯的前提下获得 trace/RAG/playbook 的统一召回。

---

## powermem_plug.py — PowerMem DataPlug 完整演示

```bash
uv run python examples/advanced/powermem_plug.py

# 使用真实 PowerMem（需安装）：
USE_POWERMEM=live uv run python examples/advanced/powermem_plug.py
```

**运行模式（`USE_POWERMEM` 环境变量）：**
- `auto`（默认）：已安装则用真实 PowerMem，否则用内置 mock
- `live`：强制使用 `pip install powermem`（内存 SQLite）
- `mock`：强制使用内置 mock 数据

演示：
- ContextSeek 作为 DataPlug socket：`PowerMemPlug` 将 PowerMem `get_all` 行转为 `ContextItem`
- `PowerMemPlug.from_records()` — 规范化 PowerMem 数据格式
- 同一 scope 下 PowerMem 记忆与 ContextSeek-native 知识的统一 `retrieve()`
- Provenance（`powermem://<id>`）与 `powermem` tag 过滤
