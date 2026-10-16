import { request } from "@playwright/test"

/**
 * 运行 e2e 之前先确认「前端页面」和「后端 API」都在。
 * 缺哪个就说清怎么补，而不是让用例在超时里糊掉。
 */
export default async function globalSetup(): Promise<void> {
  const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080"
  const context = await request.newContext({ baseURL })

  const page = await context.get("/", { timeout: 15_000 }).catch((error: unknown) => error)
  if (!(page instanceof Object) || !("status" in page)) {
    throw new Error(
      `前端页面打不开（${baseURL}）。请先运行 windows-deploy\\start.bat。原始错误：${String(page)}`,
    )
  }
  if (page.status() !== 200) {
    throw new Error(`前端页面返回 HTTP ${page.status()}（${baseURL}）。请先运行 windows-deploy\\start.bat。`)
  }

  const ready = await context.get("/api/v1/health/ready", { timeout: 15_000 })
  if (ready.status() !== 200) {
    throw new Error(
      `就绪检查失败（HTTP ${ready.status()}）。数据库或 Worker 没起来，` +
        `请先运行 windows-deploy\\start.bat。响应：${await ready.text()}`,
    )
  }

  await context.dispose()
}
