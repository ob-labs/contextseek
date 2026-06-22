# ContextSeek 控制台 (Dashboard)

一个内置的 Web 控制台，用于管理 ContextSeek 的语义记忆。包含 5 个面板：

- **检索 (Retrieve)** — 语义检索 + 展开全文
- **透视 (Insight)** — 按 scope / 阶段透视记忆，支持反馈 / forget / delete
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

`contextseek desktop-server` 启动 `contextseek.http.server` 的同源服务：
前端是预构建的静态文件，和 API 同源、同端口，无需任何代理。桌面壳
（Tauri / Electron / pywebview）只需指向本地 `127.0.0.1:8000` 即可。

## 构建 / 运行

需要 Node.js（提供 `npm`）。在仓库根目录：

```bash
make desktop-spa        # 构建前端到 dashboard/dist
make desktop-server     # 在 127.0.0.1:8000 提供 API + SPA
```

或手动：

```bash
npm --prefix dashboard install
npm --prefix dashboard run build
contextseek desktop-server --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000 。

改了前端代码后，重新 `npm --prefix dashboard run build` 即可让改动生效。

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `VITE_CTX_BASE` | API 基础路径；同源构建时留空 | `""` |
| `CTX_DESKTOP_PORT` | 未传 `--port` 时 `contextseek desktop-server` 使用的端口 | `8000` |
| `CTX_DASHBOARD_DIST` | 指定已构建 SPA 的 `dist` 目录 | 自动查找 `dashboard/dist` |

存储、模型等通用服务配置参见仓库根目录的 [.env.example](../.env.example)。

## 后续：打包成桌面应用

单进程设计便于桌面壳集成：用 Tauri / Electron / pywebview 启动
`contextseek desktop-server`（或直接嵌入 uvicorn），窗口指向 `http://127.0.0.1:8000`。
若后续要加回聊天（agentseek）能力，可恢复 `ChatPanel` 并在同一 FastAPI 进程内提供
agent 端点，保持单进程。
