import { defineConfig, devices } from "@playwright/test"

/**
 * 端到端与运行截图配置（规格书 §14.2、T28）。
 *
 * 运行前提：先执行 windows-deploy\start.bat，让数据库、API、Worker 和前端都在跑。
 * 默认使用 prod 形态的 http://127.0.0.1:8080（vite preview + /api 反代）；
 * 也可以用 E2E_BASE_URL 指向 dev 形态的 http://127.0.0.1:5173。
 *
 * 本机只下载了 Chromium 的无头 shell，因此固定 headless。
 */
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080"

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "e2e/.artifacts",
  use: {
    baseURL: BASE_URL,
    headless: true,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } },
    },
  ],
})
