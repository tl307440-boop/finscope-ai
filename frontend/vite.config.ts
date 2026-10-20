import vue from "@vitejs/plugin-vue"
import Components from "unplugin-vue-components/vite"
import { defineConfig } from "vitest/config"

declare const process: { env: Record<string, string | undefined> }

const API_TARGET = process.env.FINSCOPE_API_TARGET ?? "http://127.0.0.1:8000"

/** 子组件所在的组件目录；其余组件的目录名就是它的 kebab 名（ElButton → button）。 */
const ELEMENT_PARENT: Record<string, string> = {
  "table-column": "table",
  option: "select",
  "option-group": "select",
  "dropdown-item": "dropdown",
  "dropdown-menu": "dropdown",
  "radio-button": "radio",
  "radio-group": "radio",
  "form-item": "form",
}

/**
 * 自带解析器而不用 unplugin 的 ElementPlusResolver：后者生成 `from "element-plus/es"`（整个桶文件），
 * 桶文件顶层会执行 makeInstaller([...全部组件])，rollup 不敢删，按需引入就白做了（实测体积只差 2 KB）。
 * 这里直接指向 es/components/<目录>/index，只打进模板里真正用到的组件。
 * 目录不存在（比如写错组件名）时构建会直接报错，不会悄悄漏掉。
 */
function elementPlusComponent(name: string): { name: string; from: string } | undefined {
  if (!/^El[A-Z]/.test(name)) {
    return undefined
  }
  const kebab = name
    .slice(2)
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .toLowerCase()
  return { name, from: `element-plus/es/components/${ELEMENT_PARENT[kebab] ?? kebab}/index` }
}

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
  plugins: [
    vue(),
    // Element Plus 的 JS 按模板里实际用到的组件引入，不再 app.use 全量注册。
    // 样式刻意仍用全量 dist/index.css（main.ts 引入）：global.css 里有大量与官方样式同优先级的覆盖，
    // 依赖「官方样式先于 global.css」；按组件拆 CSS 会随页面 chunk 晚加载，反过来盖掉这些覆盖。
    Components({
      resolvers: [elementPlusComponent],
      // 不生成 components.d.ts：生成后 el-table 的插槽 row 会被 vue-tsc 当成 DefaultRow，
      // 现有 40 余处 scope.row 都要改类型，与按需引入无关；保持与全量注册时一致的（无）模板类型。
      dts: false,
    }),
  ],
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
