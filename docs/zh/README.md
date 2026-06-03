# ContextSeek 文档（中文）

面向集成与使用的操作指南，不含内部设计稿与宣传材料。

## 快速开始

| 文档 | 说明 |
|------|------|
| [安装](getting-started/installation.md) | pip、uv、extras、Docker |
| [快速上手](getting-started/quickstart.md) | 首次 `add` / `retrieve` / `expand` |
| [配置](getting-started/configuration.md) | 配置档、环境变量、LLM 分阶段上线 |

## 概念

| 文档 | 说明 |
|------|------|
| [ContextItem 对象模型](concepts/context-model.md) | 字段、Provenance、Link、统一设计理由 |
| [Scope 与 Stage](concepts/scope-and-stage.md) | 隔离边界、成熟度流水线、Stability |
| [检索模型](concepts/retrieval-model.md) | L0/L1/L2 分层、召回路由、重排 |

## 使用指南

| 文档 | 说明 |
|------|------|
| [核心概念概览](guides/core-concepts.md) | 快速概览，链接到概念文档 |
| [写入与检索](guides/write-and-retrieve.md) | 写入/检索管线、过滤、Agent 闭环、运维建议 |
| [溯源与审计](guides/provenance-and-audit.md) | 证据链、upstream、forget/delete |
| [上下文演进](guides/evolution.md) | compact、dream、feedback、overview、skill |
| [CLI 命令行（端侧）](guides/cli.md) | 个人模式：init、daemon、sync、seekdb、命令全表 |
| [存储后端](guides/storage.md) | InMemory、File、OceanBase |
| [可观测性](guides/observability.md) | 审计日志、Prometheus 指标、生产建议 |

### 集成

| 文档 | 说明 |
|------|------|
| [DataPlug](guides/integrations/dataplugs.md) | RAG / 记忆 / 轨迹；技能导入 |
| [LangChain Middleware](guides/integrations/langchain-middleware.md) | `create_agent()` Agent 的 sidecar `AgentMiddleware` |
| [MCP / HTTP / CLI](guides/integrations/mcp-http-cli.md) | 命令、路由、MCP 工具全表 |

## 参考

| 文档 | 说明 |
|------|------|
| [API 参考](reference/api.md) | 所有 `ContextSeek` 方法、参数、返回类型 |
| [配置项参考](reference/settings.md) | 所有环境变量及默认值 |
| [示例索引](reference/examples.md) | `examples/` 目录说明 |

## 故障排查

[故障排查指南](troubleshooting.md) — 安装错误、空结果、演化问题、OceanBase、调试技巧。

---

[← 文档中心](../README.md) · [English docs](../en/README.md)
