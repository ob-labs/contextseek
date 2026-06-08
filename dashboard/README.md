# ContextSeek 控制台 (Dashboard)

一个内置的 Web 控制台，用于管理 ContextSeek 的语义记忆。包含 5 个面板：

- **检索 (Retrieve)** — 语义检索 + 展开全文
- **浏览 (Browse)** — 按 scope / 阶段浏览记忆，支持反馈 / forget / delete
- **写入 (Write)** — 写入一条上下文
- **演化 / 生命周期 (Evolution)** — compact / dream / 反馈 / 删除
- **溯源图谱 (Provenance)** — 证据链 DAG 与派生回溯

前端只调用 ContextSeek 自己的 HTTP API（`contextseek.http.server`），路由都在**根路径**
（`/add`、`/retrieve`、`/health` …），不依赖 agentseek。

## 架构（单进程，面向桌面端）

```
单个 FastAPI 进程 (:8000)
  /add /retrieve /health …   -> ContextSeek API
  /  (兜底)                  -> dashboard/dist (静态 SPA)
```

`contextseek.http.dashboard` 把现有 API 路由和构建好的前端组合进**同一个进程**：
前端是预构建的静态文件，和 API 同源、同端口，无需任何代理。桌面壳
（Tauri / Electron / pywebview）只需指向本地 `:8000` 即可。

## 构建 / 运行

需要 Node.js（提供 `npm`）。在仓库根目录：

```bash
make dashboard          # 构建前端到 dashboard/dist，再单进程提供 API + SPA
```

或手动：

```bash
npm --prefix dashboard install && npm --prefix dashboard run build
contextseek-dashboard   # 等价于 uvicorn contextseek.http.dashboard:app --port 8000
```

浏览器打开 http://127.0.0.1:8000 。

改了前端代码后，重新 `npm --prefix dashboard run build` 即可让改动生效。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `VITE_CTX_DEFAULT_SCOPE` | UI 默认 scope（构建时注入） | `contextseek` |
| `CTX_SERVER_PORT` | `contextseek-dashboard` 监听端口 | `8000` |

参见 [.env.example](.env.example)。

## 后续：打包成桌面应用

单进程设计便于桌面壳集成：用 Tauri / Electron / pywebview 启动
`contextseek-dashboard`（或直接嵌入 uvicorn），窗口指向 `http://127.0.0.1:8000`。
若后续要加回聊天（agentseek）能力，可恢复 `ChatPanel` 并在同一 FastAPI 进程内提供
agent 端点，保持单进程。
