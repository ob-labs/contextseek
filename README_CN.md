# ContextSeek

[![PyPI version](https://img.shields.io/pypi/v/contextseek)](https://pypi.org/project/contextseek/)
[![PyPI downloads](https://img.shields.io/pypi/dm/contextseek)]((https://img.shields.io/pypi/dm/contextseek))
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://pypi.org/project/contextseek/)
[![License Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-community-5865F2?logo=discord&logoColor=white)](https://discord.com/invite/74cF8vbNEs)

面向 AI Agent 的语义上下文基础设施。[English](README.md)

## 概述

Agent 的自进化沿两条技术路线展开：其一，从运行行为中抽取并固化经验（如 [Hermes](https://github.com/NousResearch/hermes-agent)、[OpenHuman](https://github.com/tinyhumansai/openhuman)）；其二，不改造 Agent 执行逻辑，而演进其依赖的**上下文基础设施**——实现自动组织、持续更新与关联发现。ContextSeek 聚焦后一路径，将能力增益从任务级一次性收益，转化为上下文层的跨周期复合累积；异构 Agent 系统据此接入统一语义层，共享检索、溯源与演进能力。

要释放这一路径的价值，当前架构仍面临三类约束：**接入异构**——Memory、Trace 等组件接口与语义约定不统一；**沉淀不足**——运行经验随 Prompt 窗口消耗，难以转化为可复用能力；**溯源缺失**——输出结论缺乏可追溯的证据链。ContextSeek 定位于 LLM 与 Agent 运行时之间的统一上下文语义层，将上述能力收敛于单一对象模型——一切表示为 `ContextItem`，可检索、可溯源，并沿 `raw → extracted → knowledge → skill` 生命周期自动演进。

## 快速开始

```bash
pip install contextseek
```

```python
from contextseek import ContextSeek

ctx = ContextSeek.from_settings()  # 自动读取 .env 或环境变量

# 写入
ctx.add(
    "OceanBase 是一款金融级分布式数据库，支持 HTAP 混合负载",
    scope="acme/db/engineer",
    source="wiki",
)

# 检索（排名 SearchHit；默认返回 L1 摘要）
for hit in ctx.retrieve("分布式数据库", scope="acme/db/engineer", k=10):
    text = hit.item.summary or hit.item.content
    print(f"[{hit.item.stage.value}] score={hit.score:.2f} | {text[:100]}")
```

通过 `.env` 配置（参见 [.env.example](.env.example)）或在代码中构造 `ContextSeekSettings`。存储后端、Embedding 提供方和 LLM 是三个必要配置项。

更习惯命令行？`contextseek` CLI 用内置 `seekdb` 跑一个自包含的个人知识库，无需任何外部服务：

```bash
pip install "contextseek[seekdb]"
contextseek init                                   # 初始化 ~/.contextseek/ 及后台 daemon
contextseek sync ~/notes --scope me/work           # 导入笔记/文档（自动识别格式）
contextseek retrieve --scope me/work --query "..." # 在 CLI 检索，或通过 MCP 暴露
```

完整命令见 [CLI 指南](docs/zh/guides/cli.md)。

## 文档

- [快速上手 (ZH)](docs/zh/getting-started/quickstart.md) / [Getting started (EN)](docs/en/getting-started/quickstart.md)：安装、`.env` 配置，以及核心操作的完整演示。
- [客户端 API 参考](docs/zh/reference/api.md)：`add`、`retrieve`、`expand`、`compact`、`dream`、`evidence_chain` 等方法的完整签名。
- [配置参考](docs/zh/getting-started/configuration.md)：所有环境变量与 `ContextSeekSettings` 字段。
- [CLI 命令行（端侧）](docs/zh/guides/cli.md) / [English](docs/en/guides/cli.md)：内置 `seekdb` 的个人模式 —— `init`、后台 `daemon`、`sync` 与命令全表。
- [DataPlug 指南](docs/zh/guides/integrations/dataplugs.md)：如何从 RAG 流水线、记忆库、执行轨迹及工具注册表导入数据。
- [LangChain Middleware](docs/zh/guides/integrations/langchain-middleware.md) / [English](docs/en/guides/integrations/langchain-middleware.md)：开箱即用的 `AgentMiddleware`，把 ContextSeek 的检索、存储与 compact 接入 `create_agent()` 构建的 Agent —— 示例见下方。
- [示例](examples/README.md)：常见工作流的完整示例脚本。
- [AppWorld 评测](eval/appworld/README.md) / [τ-bench 评测](eval/taubench/README.md)：可选评测脚手架，有独立的依赖与配置要求。

## 工作原理

- **统一对象模型** — 记忆、知识、轨迹、技能全部是 `ContextItem`。每条条目携带强制 `Provenance`（来源类型、来源 id、置信度）和有类型的 `Link` 边（支持、反驳、衍生、替代），支持构建完整的 `EvidenceChain` DAG 及置信度传播。
- **内容分层** — L0（完整正文）通过 `expand()` 按需升档；L1（约 2k token）是 `retrieve()` 的默认返回面；L2（约 100 token）用于 Embedding 召回。
- **检索编排器** — 关键词 + 向量混合召回，可选 LLM 重排序，基于 scope 路由。返回排名 `SearchHit` 行，通过 `ctx.tools()` 向 OpenAI 或 Anthropic Agent 暴露工具描述。
- **EvolutionEngine** — 监测可合并、可消解冲突、可推进阶段或可提炼为技能的条目，在写入后增量运行，也可通过 `compact()` 显式触发。
- **DreamEngine** — 闲时进行模式整合与跨簇假设生成，通过 `dream()` 触发。

## 相关项目

- [seekvfs](https://github.com/ob-labs/seekvfs) — 底层虚拟文件系统

## License

[Apache License 2.0](LICENSE)
