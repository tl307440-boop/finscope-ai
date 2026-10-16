import vue from "@vitejs/plugin-vue"
import { defineConfig } from "vitest/config"

declare const process: { env: Record<string, string | undefined> }

const API_TARGET = process.env.FINSCOPE_API_TARGET ?? "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/docs": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/redoc": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/openapi.json": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 8080,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/docs": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/redoc": {
        target: API_TARGET,
        changeOrigin: true,
      },
      "/openapi.json": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "happy-dom",
    css: true,
    setupFiles: ["./tests/setup.ts"],
    // e2e 由 Playwright 运行（npm run e2e），不要被 vitest 收集。
    include: ["tests/**/*.test.ts"],
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
  },
})
