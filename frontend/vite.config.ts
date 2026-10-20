import vue from "@vitejs/plugin-vue"
import { defineConfig } from "vitest/config"

declare const process: { env: Record<string, string | undefined> }

const API_TARGET = process.env.FINSCOPE_API_TARGET ?? "http://127.0.0.1:8000"

/**
 * 第三方依赖按「更新频率」拆成稳定的 vendor 包：业务代码每次发版都会变，
 * 但 vue / element-plus / echarts 很少变，拆开后浏览器可以长期缓存它们。
 */
function vendorChunk(id: string): string | undefined {
  if (!id.includes("node_modules")) {
    return undefined
  }
  if (/[\\/]node_modules[\\/](echarts|zrender|vue-echarts|resize-detector)[\\/]/.test(id)) {
    return "vendor-echarts"
  }
  if (/[\\/]node_modules[\\/](element-plus|@element-plus|@floating-ui|@popperjs|dayjs|lodash-es|lodash-unified|async-validator|memoize-one|normalize-wheel-es|@ctrl)[\\/]/.test(id)) {
    return "vendor-element"
  }
  if (/[\\/]node_modules[\\/](vue|@vue|vue-router|pinia|@vueuse)[\\/]/.test(id)) {
    return "vendor-vue"
  }
  return undefined
}

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: { manualChunks: vendorChunk },
    },
  },
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
