import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// All known API root segments served by contextseek.http.server.
// Requests matching this prefix are proxied to the backend (port 8000) so
// that both `vite dev` and `vite preview` work without setting VITE_CTX_BASE.
const API_SEGMENTS =
  "add|retrieve|expand|forget|delete|compact|dream|feedback|upstream|" +
  "evidence_chain|chain_confidence|skill_tools|skill_context|skill_md|items|" +
  "overview|global_overview|scopes|config|metrics|plugs|seed|health|install|restart|__desktop";
const API_PROXY_PATTERN = `^/(${API_SEGMENTS})(/|$|\\?)`;
const API_PROXY_TARGET = { target: "http://127.0.0.1:8000", changeOrigin: true };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: { [API_PROXY_PATTERN]: API_PROXY_TARGET },
  },
  preview: {
    port: 3000,
    proxy: { [API_PROXY_PATTERN]: API_PROXY_TARGET },
  },
});
