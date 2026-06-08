import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Front-end and backend run as separate processes. `vite build` produces the
// static SPA in dist/; `vite preview` serves it on :3000. The backend API base
// URL is injected at build time via VITE_CTX_BASE (see src/lib/ctxClient.ts).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  preview: {
    port: 3000,
  },
});
