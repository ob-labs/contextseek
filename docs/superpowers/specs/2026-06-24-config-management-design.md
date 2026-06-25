# 配置管理（Config Management）设计

- 日期：2026-06-24（2026-06-25 增补 dashboard / HTTP / 迁移章节）
- 范围：contextseek 自身配置的托管（修改 / 溯源 / 回退）+ 接受 agentseek 配置的纳入与投影 + dashboard 配置编辑接入版本化
- 方案：A — 物化层在上（非侵入）

## 1. 背景与目标

contextseek 现有两套并行的配置体系：

1. `ContextSeekSettings`（`src/contextseek/config/settings.py`）—— pydantic-settings，从 `.env` / 环境变量加载（分节前缀 `STORAGE_` / `LLM_` / `OB_` 等），SDK / 开发者使用。
2. `RuntimeConfig`（`src/contextseek/config/runtime.py`）—— JSON，经 `CONTEXTSEEK_CONFIG` 加载，daemon / http 服务入口使用，含 strategy / api_keys / OceanBase 参数。

两者靠 `to_strategy_config` / `settings_config` 桥接。目前**没有任何版本历史 / 溯源 / 回退机制**——`src/contextseek/daemon/init_cmd.py:179` 仅在覆盖前做一个单文件 backup。

agentseek 侧：agentseek 自身用 `AGENTSEEK_*` env + `BUB_*` 别名 + `config.yml`（`DEFAULT_AGENTSEEK_CONFIG`）；contrib 的 `agentseek-contextseek/config.py` 用反射 `ContextSeekSettings` 来发现 contextseek 真正消费的 env vars，让 `AGENTSEEK_CTX_*` 作为 contextseek env 的 fallback，并桥接 LLM 凭证。该机制是隐式的、运行时改 `os.environ`，无版本化与溯源。

dashboard 侧：React SPA（`dashboard/`，含 Tauri 桌面壳），已有 `SettingsPanel` 通过 HTTP API 编辑配置：`GET /config` 读实时 `ContextSeekSettings()` 返回扁平 `Config` 结构；`PUT /config` 把字段映射成 env 变量后调用 `_update_env_file`（`src/contextseek/http/server.py:820`）**原地合并写 `.env`**，返回 `restart_required`；`POST /config/test` 测试连接。该路径**直接改 `.env`，无版本 / 溯源 / 回退**——正是配置管理要补的洞。

### 目标

- 统一托管上述两层配置：可修改、可溯源、可回退。
- 接受 agentseek 的配置（纳入 + 投影）：agentseek 仍是上游自主配置，contextseek 只做受控 pull / diff / 版本化 / 投影，不反写。
- dashboard 的配置编辑接入版本化：`PUT /config` 改为走 `ConfigManager`，每次编辑成为可溯源可回退的版本；dashboard 内嵌版本历史 / diff / 回退 / blame / override 来源 / 漂移 / agentseek 摄入。
- 不重写现有加载器核心逻辑，风险可控。
- 避免引导循环：配置决定存储后端，而配置历史不能依赖存储后端。

### 非目标

- 不做 agentseek → contextseek 的反向写回。
- 不替换 pydantic-settings 的 source 机制。
- 不在本期把配置版本存为 ContextItem（保持独立文件历史；后续可选导出为 ContextItem 做关联）。

## 2. 整体架构

新增 `config_manager` 子包（`src/contextseek/config/manager.py` 等），作为现有两套加载器**之上**的权威版本化层。

```
                 ┌──────────────────────────────────────────┐
   CLI ────────► │  ConfigManager (权威源, append-only 历史) │
  contextseek    │  · native 层 (contextseek 自有)          │
  config …       │  · projected 层 (agentseek 投影而来)     │
                 │  · 版本链 + 溯源元数据                   │
                 └───────────────┬──────────────────────────┘
                                 │ apply
                    ┌────────────▼─────────────┐
   AgentseekIngestor│  Materializer (物化器)    │
   (pull/diff/proj) │  → .env   (ContextSeekSettings)
                    │  → config.json (RuntimeConfig / CONTEXTSEEK_CONFIG)
                    └────────────┬─────────────┘
                                 │ 现有加载器原样读取
                   ContextSeekSettings / load_runtime_config / factory
```

- 托管库固定路径 `${CONTEXTSEEK_HOME:-.contextseek}/config/`，**不依赖 VFS / 存储后端**，无引导循环。
- 现有 `settings.py` / `runtime.py` / `factory.py` **不改核心逻辑**，仅在物化文件被写出后照常加载。
- 配置修改永远经 `ConfigManager`，`apply` 才物化到 `.env` / `config.json`。

## 3. 数据模型

### 3.1 目录布局

```
.contextseek/config/
├── current.json          # 当前生效的统一配置文档（权威源）
├── history/
│   ├── v000001.json      # 完整快照 + 溯源元数据
│   ├── v000002.json
│   └── …
├── manifest.jsonl        # append-only 版本索引（id/parent/created/origin/reason/hash）
└── sources/
    └── agentseek.json    # 最近一次摄入的 agentseek 原始配置快照（供 diff/溯源）
```

### 3.2 版本文件结构

每个版本文件（`vNNNNNN.json`）：

```jsonc
{
  "version_id": "v000003",
  "parent_version_id": "v000002",
  "created_at": "2026-06-24T18:00:00Z",   // UTC ISO 时间戳，由 manager 写入
  "origin": "manual | agentseek-projection | migration | rollback",
  "author": "cli:tq | agentseek:v0.4 | system",
  "reason": "raise retrieval default_k to 20",
  "source_ref": "agentseek@config.yml:sha256:…",  // 仅 projected 来源
  "payload_hash": "sha256:…",
  "payload": {
    "native":   { "storage": {}, "llm": {}, "ob": {}, "runtime": {}, "strategy": {} },
    "projected":{ "llm": {"provider":"openai","model":"gpt-4o"}, "storage": {} },
    "effective":{ /* native 叠加 projected 后的合并结果，物化器直接用 */ }
  },
  "diff": { "added": [], "changed": [], "removed": [] }   // vs parent
}
```

- `native` = contextseek 自有配置；`projected` = agentseek 映射产物；`effective` = 合并结果。
- `effective` 中每条 key 记录 `override_source`（`native` / `projected:agentseek`），便于 `status` / `blame` 展示来源。

### 3.3 合并优先级

`projected`（agentseek）作为基线，`native`（contextseek）中显式设值的 key 覆盖 `projected`：

- key 在 `native` 显式设值 → `effective` 取 `native`，`override_source=native`。
- key 未在 `native` 设值 → `effective` 取 `projected`，`override_source=projected:agentseek`。

即 contextseek 总能显式 override agentseek 投影值；同一 key 被两边设置时不报错，`effective` 取 native，`status` 列出「override 冲突」供人知悉。

### 3.4 manifest.jsonl

append-only 版本索引，每行一个版本摘要（`version_id` / `parent_version_id` / `created_at` / `origin` / `reason` / `payload_hash`）。用于 `history` 快速列出与 `verify` 链校验；完整 payload 在 `history/v*.json`。

## 4. CLI

新增 `contextseek config` 子命令组，挂在现有 `src/contextseek/cli/main.py`：

| 命令 | 作用 |
|---|---|
| `contextseek config show [--version N] [--layer native\|projected\|effective]` | 显示某版本某层配置 |
| `contextseek config set <key> <value> [--reason …]` | 修改 native 单项，产生新版本并 apply |
| `contextseek config set --file <path>` | 批量导入 native（JSON/env） |
| `contextseek config apply` | 把 current.json 物化为 `.env` + `config.json` |
| `contextseek config history [-n 10]` | 列出版本链（id/created/origin/reason） |
| `contextseek config diff <vA> <vB>` | 两版本或版本 vs 当前的 diff |
| `contextseek config rollback <version> [--reason …]` | 以指定版本 payload 创建新版本并 apply（append-only） |
| `contextseek config redo` | 撤销最近一次 rollback（以被回退的版本再建新版本） |
| `contextseek config blame <key>` | 展示该 key 最后一次变更的版本 / origin / author / reason / source_ref |
| `contextseek config status` | 当前版本、物化文件是否漂移、agentseek 源是否过期 |
| `contextseek config ingest agentseek [--path …] [--apply]` | 拉取 agentseek 配置，diff / 版本化 / 投影，可选 apply |
| `contextseek config verify` | 校验 history 完整性（hash 链、parent 链） |
| `contextseek config import [--from-env] [--from-runtime]` | 把现有 `.env` / `config.json` 导入为 `native` 的 v1（`origin=migration`）；首次接入托管库的迁移步骤 |

## 5. agentseek 摄入与映射表

`AgentseekIngestor` 负责 pull → diff → 版本化 → 投影。复用并升级现有 contrib 的反射思路，但把「隐式 env 别名」升级为**显式声明式映射表**。

### 5.1 摄入源（按优先级探测）

1. `--path` 指定的 agentseek `config.yml`（agentseek 的 `DEFAULT_AGENTSEEK_CONFIG`）。
2. 进程内 `AGENTSEEK_*` / `BUB_*` 环境变量。
3. agentseek 包导出的 settings（若可导入 `agentseek.env.get_agentseek_settings()`）。

### 5.2 映射表

`mapping.py` 中声明，可测试、可扩展：

```python
# agentseek 键  →  contextseek native 路径  +  转换函数  +  provider hint
AGENTSEEK_MAPPING = {
    "AGENTSEEK_API_KEY":  ("llm.api_key",    lambda v: v, "openai"),
    "AGENTSEEK_API_BASE": ("llm.base_url",   lambda v: v, None),
    "AGENTSEEK_MODEL":    ("llm.model",      _strip_provider_prefix, _detect_provider),
    "AGENTSEEK_HOME":     ("runtime.storage_path", _home_to_store, None),
    # 仅当 contextseek LLM 启用时才投影凭证
}
```

- provider 检测、`LLM_CLASS_PATH` 推导、`AGENTSEEK_CTX_*` 反射 fallback 全部迁移自 contrib `agentseek_contextseek/config.py`，但产出写入 `projected` 层而非临时改 `os.environ`。
- **投影幂等**：同一 agentseek 源（按 `sha256(config.yml)` 去重）多次 ingest 不产生新版本（`source_ref` 相同时跳过），避免无意义历史膨胀。
- **不反向写 agentseek**：agentseek 始终是上游自主配置，contextseek 只读 + 投影 + 溯源。
- 摄入产生 `origin=agentseek-projection` 版本，`source_ref` 记录源文件 hash，溯源链可直接追到 agentseek 的某次配置变更。

## 6. 溯源与回退

### 6.1 溯源链

`manifest.jsonl` 即版本链（`version_id → parent_version_id`）。任意 key 的来源可查：`effective` 中每条记录带 `override_source`，`contextseek config blame <key>` 展示该 key 最后一次变更的版本、origin、author、reason、source_ref。

### 6.2 回退（append-only）

```
rollback(v000002)
  → 读 v000002.payload
  → 新建 v000005, parent=v000004(当前), origin=rollback,
    reason="rollback to v000002", payload=v000002.payload
  → current.json = v000005.payload
  → apply 物化
```

历史 v000003 / v000004 保留可查，永不丢。`contextseek config redo` 可回滚这次回退（即以 v000004 再建新版本）。

### 6.3 漂移检测

`status` 比较 `current.json` 物化出的期望 `.env` / `config.json` 与磁盘实际文件 hash，不一致即报「drifted」（有人手改了物化文件），提示 `config apply` 重物化或 `config set` 正式纳入。

### 6.4 迁移 / 导入（首次接入）

托管库采用**全量重写**物化 `.env`（只写 `effective` 里有的 key）。为避免覆盖 `.env` 中尚未被托管库跟踪的 key，首次接入必须先迁移：`contextseek config import` 把现有 `.env`（经 envreflector 反演成 `section.field`）与 `config.json`（`runtime` 节）导入为 `native` 的 v1（`origin=migration`）。迁移后托管库即完整，全量物化与漂移检测才安全。

- 反演规则：env var 名 → `(section, field)`（envreflector 的逆向）；无法反演的 key（非 settings 字段）保留到 `native._extra_env` 透传区，物化时原样写回，确保不丢。
- `GET /config` 与 `PUT /config` 在托管库为空时自动触发一次迁移（懒迁移），保证 dashboard 首次可用即纳入版本化。

## 7. HTTP API（dashboard 接入）

在 `src/contextseek/http/server.py` 现有 `/config` 端点基础上：

- **`GET /config`**：仍返回现有扁平 `Config` 形状（向后兼容 dashboard `Config` 类型），但**扩充**字段：`config_version`、`override_sources`（key → `native` / `projected:agentseek`）、`drift`（env/runtime 是否漂移）、`agentseek_source_ref` / `agentseek_stale`。数据源从「实时 `ContextSeekSettings()`」改为「托管库 `current().effective`」（托管库为空时先懒迁移）。backend 特定字段（ob_/seekdb_/sqlite_/storage_path）从 effective 对应 section 派生，保持原形状。
- **`PUT /config`**：**重路由**——把 `ConfigUpdateRequest` 的扁平字段经 `FIELD_TO_ENV` → env → `(section, field)` 反演成 dotted 路径，调用 `ConfigManager.set_native_many(...)`，再 `apply(materializer)`。每次编辑成为一个版本（`origin=manual`，`author=dashboard`），返回 `{status, version_id, restart_required: true}`。不再直接调 `_update_env_file`（该函数保留为迁移/回退兜底）。
- **`POST /config/test`**：不变（仍测试连接，不落库）。
- 新增只读 / 操作端点：
  - `GET /config/history?n=` → 版本链摘要列表
  - `GET /config/version/{id}?layer=` → 某版本某层
  - `GET /config/diff?a=&b=` → 两版本 diff
  - `GET /config/blame?key=` → 某 key 溯源
  - `POST /config/rollback` body `{version, reason}` → append-only 回退 + apply
  - `POST /config/redo` body `{reason}`
  - `GET /config/status` → 当前版本 / 漂移 / agentseek 源过期 / verify 问题
  - `GET /config/verify`
  - `POST /config/ingest/agentseek` body `{path?, apply?}` → 摄入并可选 apply
- 所有端点复用现有 FastAPI app 注册风格；`ConfigManager` 实例按 `${CONTEXTSEEK_HOME:-.contextseek}/config` 解析，HTTP 进程内单例。

## 7.5 dashboard UI

`dashboard/src/panels/SettingsPanel.tsx` 现有编辑区保留（提交即版本化），下方内嵌「版本历史」区：

- 版本链列表（version_id / created_at / origin / author / reason），每行可展开看 diff、一键 `POST /config/rollback`。
- 当前每个配置项标注 override 来源徽章（`native` / `projected:agentseek`），冲突项高亮。
- 顶部状态条：当前版本号、漂移指示（drift）、agentseek 源是否过期；「摄入 agentseek」按钮触发 `POST /config/ingest/agentseek`。
- `blame`：点击某配置项显示其最近变更版本。
- 新增 `ctxClient` 方法（`getConfigHistory` / `getConfigVersion` / `getConfigDiff` / `getConfigBlame` / `rollbackConfig` / `redoConfig` / `getConfigStatus` / `verifyConfig` / `ingestAgentseek`）与对应 TypeScript 类型，复用现有 `get/post/put` 封装（自动兼容 Tauri 桌面壳）。

## 8. 错误处理

- **写原子性**：先写 `history/vN.json.tmp` → fsync → rename → append `manifest.jsonl`（fsync）→ 更新 `current.json`。任一步崩溃：启动时 `verify` 扫描，孤儿 tmp 清理；manifest 已 append 但 current 未更新 → 以 manifest 最后一行为准回放。
- **hash 链校验**：每个版本 `payload_hash = sha256(payload)`，`verify` 校验链上每条 hash 与文件一致，篡改可发现。
- **加载失败保护**：`apply` 物化前先校验 `effective` 能被 `ContextSeekSettings` / `RuntimeConfig` 成功构造（dry-run validate），校验失败则不写物化文件、版本标记 `failed`，保留前一生效配置。
- **映射冲突**：同一 contextseek key 被 agentseek 投影但 native 已 override → 不报错，`effective` 取 native，`status` 列出「override 冲突」供人知悉。

## 9. 测试

pytest，跟随仓库现有风格：

- `tests/unit_tests/test_config_manager.py`：版本递增、append-only、hash 链、parent 链、rollback 不删历史、redo。
- `tests/unit_tests/test_config_materializer.py`：`.env` 与 `config.json` 物化正确、dry-run validate 拦截非法配置、漂移检测、`_extra_env` 透传。
- `tests/unit_tests/test_config_mapping.py`：映射表各分支、provider 检测。
- `tests/unit_tests/test_config_agentseek_ingestor.py`：幂等摄入（同 hash 不新增版本）、source_ref 溯源。
- `tests/unit_tests/test_config_cli.py`：show / set / apply / history / diff / rollback / blame / status / ingest / verify / import 端到端。
- `tests/unit_tests/test_config_http.py`：`GET /config` 扩充字段、`PUT /config` 重路由产版本、history/diff/rollback/blame/ingest 端点、懒迁移。
- 崩溃恢复：模拟 manifest append 后 current 未更新，验证启动修复。
- dashboard：`dashboard/src/panels/SettingsPanel.tsx` 历史区组件的轻量单元测试（若仓库已有前端测试设施；否则手动 build 校验 `tsc -b` 通过）。

## 10. 文件改动概览

新增：
- `src/contextseek/config/envreflector.py` — 反射 settings → env 名（含逆向 env→section.field）。
- `src/contextseek/config/manager.py` — `ConfigManager`（版本化权威源）。
- `src/contextseek/config/materializer.py` — `Materializer`（物化 + dry-run + 漂移 + `_extra_env` 透传）。
- `src/contextseek/config/mapping.py` — 显式映射表 + provider 检测（迁移自 contrib）。
- `src/contextseek/config/agentseek_ingestor.py` — `AgentseekIngestor`（pull / diff / 投影）。
- `src/contextseek/config/migrator.py` — `import_existing(env_path, runtime_path) -> native dict`（迁移/导入）。
- `src/contextseek/config/cli.py` — `contextseek config` 子命令组（含 `import`）。
- `src/contextseek/http/config_routes.py` — 配置管理 HTTP 端点（注册到现有 app）。
- `tests/unit_tests/test_config_*.py` — 上述测试。
- dashboard：`dashboard/src/panels/components/ConfigHistorySection.tsx`（历史区组件）；`dashboard/src/lib/types.ts` 新增类型；`dashboard/src/lib/ctxClient.ts` 新增方法。

改动：
- `src/contextseek/cli/main.py` — 注册 `config` 子命令组 + 分发。
- `src/contextseek/http/server.py` — `GET /config` 扩充字段并改读托管库（懒迁移）；`PUT /config` 重路由走 ConfigManager；注册新端点。
- `src/contextseek/config/__init__.py` — 导出新增公共 API。
- `dashboard/src/panels/SettingsPanel.tsx` — 内嵌历史区、override 徽章、漂移/摄入按钮。
- 现有 `settings.py` / `runtime.py` / `factory.py` 核心逻辑不动；`_update_env_file` 保留为迁移兜底。

## 11. 决策记录

- **托管范围**：统一托管两层（`ContextSeekSettings` + `RuntimeConfig`）。
- **历史持久化**：专用文件历史（`.contextseek/config/`），不依赖 VFS，避免引导循环。
- **agentseek 关系**：纳入 + 投影（单向，不反写）。
- **架构衔接**：方案 A — 物化层在上，非侵入，现有加载器原样读取物化文件。
- **合并优先级**：projected 作基线，native 显式值覆盖。
- **回退语义**：append-only，回退 = 新建等于旧版本 payload 的新版本，历史永不删。
- **dashboard 接入**：迁移 + 重路由（`PUT /config` 走 ConfigManager，全量物化，懒迁移首次接入）；UI 在 SettingsPanel 内嵌历史区。
