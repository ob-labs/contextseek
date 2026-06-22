# 存储后端

ContextSeek 与存储无关。根据部署阶段选择合适的后端；切换时只需修改配置，无需改动代码。

---

## SQLiteBackend

未配置 `STORAGE_BACKEND` 时的默认选项。数据保存在本地 SQLite 文件中，无需额外服务或原生引擎。

**适用场景：** 个人端侧使用、桌面端、本地 Agent、需要稳定持久化的默认安装。

```python
from contextseek import ContextSeek

ctx = ContextSeek.from_settings()  # 默认使用 SQLite
```

或通过配置显式指定：

```env
STORAGE_BACKEND=sqlite
SQLITE_PATH=~/.contextseek/contextseek.sqlite3
```

---

## InMemoryBackend

所有数据保存在内存字典中，进程退出后丢失。

**适用场景：** 单元测试、快速原型验证、不需要持久化的单次请求流水线。

```python
from contextseek import ContextSeek

ctx = ContextSeek()
```

或通过配置显式指定：

```env
STORAGE_BACKEND=memory
```

---

## FileBackend

将每条 `ContextItem` 作为 JSON 文件持久化到本地目录。底层依赖 [seekvfs](https://github.com/oceanbase/seekvfs)。

**适用场景：** 本地开发、单进程 Agent、需要跨运行保留轨迹的 CI 流水线。

```env
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/store
```

目录在首次写入时自动创建。每个 scope 对应一个子目录，每条 item 是以 item ID 命名的单个 `.json` 文件。

```python
from contextseek import ContextSeek, ContextSeekSettings
from contextseek.config.settings import StorageSettings

ctx = ContextSeek.from_settings(
    ContextSeekSettings(
        storage=StorageSettings(backend="file", path="/data/my-agent")
    )
)
```

**Embedding 支持：** `FileBackend` 不支持向量召回，仅支持关键词匹配。需要语义检索时请切换到 OceanBase 并配置 Embedder。

---

## OceanBase 后端

生产环境后端，支持 HNSW 近似向量搜索与全文检索（FTS），开箱即用混合召回。

**适用场景：** 多进程部署、生产 Agent、需要向量召回或高吞吐写入的场景。

**安装扩展：**

```bash
pip install contextseek[oceanbase]
```

**配置：**

```env
STORAGE_BACKEND=oceanbase
STORAGE_OB_URI=mysql+pymysql://user:pass@host:2881/dbname
STORAGE_OB_TABLE=context_items
```

配合 Embedding 提供方使用，`retrieve()` 会自动启用混合召回：

```env
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_API_KEY=sk-...
```

检索编排器在检测到 Embedder 后自动激活 `vector` 召回路由。

---

## 分层存储（冷热分层）

`TieredSeekVFSAdapter` 封装两个后端：热层存储近期/活跃数据，冷层存储归档内容。

```env
STORAGE_BACKEND=file
STORAGE_PATH=.contextseek/hot
STORAGE_COLD_BACKEND=file
STORAGE_COLD_PATH=.contextseek/cold
```

写入始终进入热层。`compact()` 将已归档条目（超过 TTL 的 ephemeral 条目、软删除条目、低重要性陈旧条目）迁移至冷层。`retrieve()` 默认不读取冷层中的已删除条目（`include_deleted=False`）。

> **注意：** 热层到冷层的自动迁移只在 `compact()` 时触发，条目不会自动迁移。

---

## 基于 hash 的查找

每个序列化后的 `ContextItem` 都包含内容 hash。能够索引该字段的存储适配器会提供
`find_by_hash(prefix, hash_value)`，作为在指定 scope 前缀内执行精确查找的可选快速路径。

ContextSeek 在写入新条目时，会先尝试使用该查找能力，再进入完整冲突检测。如果同一 scope
内已经存在相同内容，写入可以幂等地返回已有条目，而不必扫描整个 scope 或创建重复数据。
导入和同步流程也可以复用 hash，在重复运行时跳过未变化的记录。

无法高效维护 hash 索引的后端可以不提供该快速路径；调用方会回退到常规扫描或搜索行为。
hash 查找用于精确内容复用和去重，不用于语义相似度判断。

---

## 后端选型参考

| 场景 | 推荐后端 |
|---|---|
| 单元测试 / CI | `InMemoryBackend` |
| 本地开发、单进程 | `FileBackend` |
| seekdb 嵌入式模式 | `STORAGE_BACKEND=seekdb`|
| seekdb server mode | `STORAGE_BACKEND=oceanbase` |
| 语义 / 向量检索 | OceanBase |
| 生产多进程部署 | OceanBase |
| 长期归档 | 分层（File + File 或 OceanBase + File）|

---

## 向量召回的 Embedding 要求

向量召回需要同时满足：

1. 已配置 Embedding 提供方（如 `EMBEDDING_PROVIDER=openai`）
2. 使用支持向量存储和查询的后端（OceanBase；`FileBackend` 仅回退到关键词匹配）

若 `EMBEDDING_PROVIDER=none`（默认），`retrieve()` 仅使用短语和词项匹配。

---

[← 写入与检索](../write-and-retrieve.md) · [配置](../../getting-started/configuration.md) · [API 参考](../../reference/api.md)
