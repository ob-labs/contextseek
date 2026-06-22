# 配置项参考

所有配置均通过环境变量和可选的 `.env` 文件加载。零配置默认为本地 SQLite 存储 + 仅关键词检索，无需任何 API Key。

将 `.env.example` 复制为 `.env` 并编辑。`.env` 的解析顺序和代码构造方式见[配置](../getting-started/configuration.md)。

---

## 存储（`STORAGE_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STORAGE_BACKEND` | `sqlite` | 存储后端：`sqlite`、`memory`、`file`、`seekdb` 或 `oceanbase` |
| `STORAGE_PATH` | `.contextseek/store` | `backend=file` 时的根目录 |
| `SQLITE_PATH` | `~/.contextseek/contextseek.sqlite3` | `backend=sqlite` 时的 SQLite 文件 |
| `STORAGE_URI_SCHEME` | `contextseek://` | Scope 引用的 URI scheme |
| `STORAGE_COLD_BACKEND` | _(空)_ | 可选冷层后端类型；空值禁用分层存储 |
| `STORAGE_COLD_PATH` | `.contextseek/cold` | 冷层文件后端根目录 |

OceanBase 见下方 `OB_*` 部分及[存储后端](../guides/storage.md)。

## OceanBase（`OB_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OB_HOST` | `127.0.0.1` | OceanBase 主机 |
| `OB_PORT` | `2881` | OceanBase 端口 |
| `OB_USER` | `root@test` | 连接用户 |
| `OB_PASSWORD` | _(空)_ | 连接密码 |
| `OB_DB_NAME` | `test` | 数据库名 |
| `OB_TABLE_NAME` | `contextseek_items` | 向量表名 |

OceanBase 通过运行时工厂或 `from_runtime_config()` 实例化，默认 `from_settings()` 路径不读取这些变量。

## 向量嵌入（`EMBEDDING_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EMBEDDING_PROVIDER` | `none` | `none`、`openai`、`dashscope`、`ollama`、`huggingface` 或 `langchain` |
| `EMBEDDING_CLASS_PATH` | _(空)_ | 可选自定义 LangChain 类，如 `langchain_openai.OpenAIEmbeddings` |
| `EMBEDDING_MODEL` | _(空)_ | 传给 provider 构造函数的模型名 |
| `EMBEDDING_DIMS` | `0` | 向量维度；已知 provider 可省略并自动推断 |
| `EMBEDDING_KWARGS` | `{}` | 传给 provider 构造函数的额外参数（JSON 对象） |

Provider 的 API Key（`OPENAI_API_KEY`、`DASHSCOPE_API_KEY` 等）由 LangChain 类直接读取，ContextSeek 不处理。

## LLM（`LLM_*`）

共享 LLM 客户端，用于：重排、摘要生成、演化引擎、dream 引擎、冲突判断。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `none` | `none`、`openai`、`dashscope`、`ollama` 或 `langchain` |
| `LLM_CLASS_PATH` | _(空)_ | 可选自定义 LangChain 类，如 `langchain_openai.ChatOpenAI` |
| `LLM_MODEL` | _(空)_ | Chat 模型名 |
| `LLM_KWARGS` | `{}` | 传给 provider 构造函数的额外参数（JSON 对象） |

自定义 LangChain 类可使用 `provider=langchain` 并配置 `*_CLASS_PATH`。

## 摘要生成（`SUMMARIZER_*`）

控制每次 `add()` 时 L2 `abstract` 和 L1 `summary` 的生成。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SUMMARIZER_PROVIDER` | `llm` | `none`（禁用）或 `llm`（使用 `LLM_*`） |
| `SUMMARIZER_L2_MAX_CHARS` | `100` | L2 abstract 字符预算 |
| `SUMMARIZER_L1_MAX_CHARS` | `2000` | L1 summary 字符预算 |

`SUMMARIZER_PROVIDER=llm` 但未配置 LLM 时，摘要生成被跳过，发出一次警告，检索退化为仅 L0 模式。

## 检索（`RETRIEVAL_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `RETRIEVAL_DEFAULT_K` | `20` | 默认候选池大小 |
| `RETRIEVAL_RECALL_ROUTES` | `["phrase","terms"]` | JSON 列表：`phrase`、`terms`、`vector` |
| `RETRIEVAL_CANDIDATE_MULTIPLIER` | `4` | 重排前的超采样倍数 |
| `RETRIEVAL_VECTOR_WEIGHT` | `0.7` | 向量相似度的混合分权重 |
| `RETRIEVAL_FTS_WEIGHT` | `0.3` | 全文搜索的混合分权重 |
| `RETRIEVAL_TERM_WEIGHT` | `0.15` | 词条重叠对启发式得分的贡献 |
| `RETRIEVAL_RECENCY_WEIGHT` | `0.05` | 时近性对启发式得分的贡献 |
| `RETRIEVAL_FEEDBACK_WEIGHT` | `0.20` | `relevance_boost` 对启发式得分的贡献 |
| `RETRIEVAL_ARCHIVE_PENALTY` | `0.50` | 已归档条目的得分乘数 |
| `RETRIEVAL_PROVENANCE_WEIGHT` | `0.15` | Provenance 置信度贡献 |
| `RETRIEVAL_LINK_BOOST` | `0.10` | 有支持链接的条目的得分加成 |
| `RETRIEVAL_LINK_REFUTE_PENALTY` | `0.40` | 有反驳链接的条目的得分惩罚 |
| `RETRIEVAL_LINK_SUPERSEDE_PENALTY` | `0.35` | 已被替代条目的得分惩罚 |
| `RETRIEVAL_RERANKER_MODE` | `heuristic` | `heuristic` 或 `llm` |
| `RETRIEVAL_LLM_RERANK_TOP_N` | `20` | 传给 LLM 重排器的候选数量 |

## 演化（`EVOLUTION_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EVOLUTION_ENABLED` | `false` | 总开关——启用完整 `compact()` 流水线 |
| `EVOLUTION_DEDUPE_BY_HASH` | `true` | `compact()` 时做哈希精确去重 |
| `EVOLUTION_SEMANTIC_MERGE` | `true` | `compact()` 时做相似度合并聚类 |
| `EVOLUTION_SEMANTIC_MERGE_THRESHOLD` | `0.72` | 合并聚类的余弦相似度阈值 |
| `EVOLUTION_MIN_CLUSTER_SIZE` | `3` | 形成合并簇所需的最少条目数 |
| `EVOLUTION_DECAY_HALF_LIFE_DAYS` | `7.0` | importance 衰减半衰期（天） |
| `EVOLUTION_EXTRACT_MIN_AGE_SECONDS` | `60.0` | 条目最小年龄，超过后才尝试提取 |
| `EVOLUTION_DISTILL_MIN_USE_COUNT` | `10` | 技能蒸馏所需的最低 `access_count` |
| `EVOLUTION_DISTILL_MIN_RELEVANCE_BOOST` | `1.2` | 技能蒸馏所需的最低 `relevance_boost` |
| `EVOLUTION_EPHEMERAL_TTL_SECONDS` | `3600.0` | 临时条目归档前的 TTL |
| `EVOLUTION_LLM_MERGE_ENABLED` | `false` | LLM 辅助聚类合并合成 |
| `EVOLUTION_LLM_CONFLICT_CHECK_ENABLED` | `false` | 写入时 LLM 矛盾判断 |
| `EVOLUTION_LLM_STAGE_INFER_ENABLED` | `false` | 写入时 LLM stage 分类 |
| `EVOLUTION_LLM_DISTILL_ENABLED` | `false` | LLM 技能蒸馏 |
| `EVOLUTION_LLM_FEEDBACK_ENABLED` | `false` | LLM 反馈原因解析 |

## Dream（`DREAM_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DREAM_LLM_ENABLED` | `false` | dream 周期中启用 LLM 辅助整合与发散 |

## Prompt 模板（`PROMPT_*`）

覆盖任意 LLM Prompt 模板。占位符因模板而异（`{query}`、`{content}`、`{items}` 等）。JSON 示例中的字面花括号需转义为 `{{` 和 `}}`。

| 变量 | 模板用途 |
|------|----------|
| `PROMPT_SUMMARIZER_ABSTRACT_TEMPLATE` | L2 abstract 生成 |
| `PROMPT_SUMMARIZER_SUMMARY_TEMPLATE` | L1 summary 生成 |
| `PROMPT_RETRIEVAL_RELEVANCE_TEMPLATE` | LLM 重排评分 |
| `PROMPT_CONFLICT_JUDGE_TEMPLATE` | 写入时矛盾检测 |
| `PROMPT_STAGE_CLASSIFIER_TEMPLATE` | 写入时 LLM stage 推断 |
| `PROMPT_FEEDBACK_TAG_TEMPLATE` | 反馈原因解析 |
| `PROMPT_MERGE_SYNTHESIS_TEMPLATE` | 聚类合并合成 |
| `PROMPT_DISTILL_CANDIDATE_TEMPLATE` | 技能蒸馏候选评分 |
| `PROMPT_DISTILL_RENDER_TEMPLATE` | 技能蒸馏渲染 |
| `PROMPT_DREAM_CONSOLIDATION_TEMPLATE` | Dream 整合步骤 |
| `PROMPT_DREAM_DIVERGENCE_TEMPLATE` | Dream 发散/假设步骤 |

完整模板键和占位符变量见 `.env.example` 中的注释块。

## 安全（`SECURITY_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SECURITY_ACL_ENABLED` | `true` | 启用读写 ACL 策略 |
| `SECURITY_ALLOW_ANY_SOURCE` | `true` | `false` 时仅允许 `SECURITY_ALLOWED_SOURCES` 中的来源写入 |
| `SECURITY_ALLOWED_SOURCES` | `[]` | 允许写入的来源标识 JSON 列表 |
| `SECURITY_REDACT_SENSITIVE` | `false` | 写入时脱敏匹配字段 |
| `SECURITY_REDACTION_TOKEN` | `[REDACTED]` | 脱敏替换符 |
| `SECURITY_REDACT_FIELDS` | `[]` | 需脱敏的字段名 JSON 列表 |
| `SECURITY_DROP_FIELDS` | `[]` | 需完全丢弃的字段名 JSON 列表 |

## 可观测性（`OBSERVABILITY_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OBSERVABILITY_AUDIT_ENABLED` | `false` | 启用 JSONL 审计日志 |
| `OBSERVABILITY_AUDIT_PATH` | `.contextseek/audit.jsonl` | 审计日志文件路径 |
| `OBSERVABILITY_METRICS_ENABLED` | `false` | 启用 Prometheus 文本指标导出 |
| `OBSERVABILITY_METRICS_PATH` | `.contextseek/metrics.prom` | 指标文件路径 |
| `OBSERVABILITY_TRACE_SAMPLE_RATE` | `1.0` | 追踪采样率（0.0–1.0） |

## 生命周期（`LIFECYCLE_*`）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LIFECYCLE_INTERVAL_SECONDS` | `3600.0` | 调度器检查间隔（秒） |
| `LIFECYCLE_AUTO_COMPACT` | `true` | 允许调度器触发压缩 |
| `LIFECYCLE_COMPACT_MIN_ITEMS` | `5` | 触发压缩所需的最低条目数 |

## Scope 规范性检查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SCOPE_LINT` | `false` | 调用 `ctx.add()` 时检查 scope 格式，不规范时发出 `ScopeLintWarning` |

`SCOPE_LINT=true` 时触发的检查规则：

| 场景 | 警告内容 |
|------|----------|
| scope 为空字符串 | 强烈建议使用层级 scope |
| scope 无 `/`（完全扁平） | 建议至少两层层级以便隔离 |
| scope 深度超过 6 层 | 层级过深，可能导致检索范围过窄 |
| scope 中含空格或大写字母 | 建议使用小写 kebab-case |

此检查默认关闭，建议仅在开发环境启用。也可在代码中直接配置：

```python
from contextseek import ContextSeek
from contextseek.config.settings import ContextSeekSettings

ctx = ContextSeek.from_settings(ContextSeekSettings(scope_lint=True))
```

---

## 快速参考：最小生产 `.env`

```env
# 存储
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/data

# 向量嵌入（OpenAI 示例）
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...

# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini

# 检索
RETRIEVAL_RECALL_ROUTES=["phrase","terms","vector"]

# 第一阶段 LLM 功能
RETRIEVAL_RERANKER_MODE=llm
RETRIEVAL_LLM_RERANK_TOP_N=20
DREAM_LLM_ENABLED=true

# 可观测性
OBSERVABILITY_AUDIT_ENABLED=true

# 演化（第一阶段稳定后再启用）
EVOLUTION_ENABLED=true
```

启用所有 LLM 功能前请参阅[分阶段 LLM 上线](../getting-started/configuration.md#分阶段-llm-上线)。

---

[← 配置](../getting-started/configuration.md) · [API 参考](api.md) · [存储后端](../guides/storage.md)
