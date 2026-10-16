import { mkdirSync } from "node:fs"
import path from "node:path"
import { expect, test, type Page } from "@playwright/test"
import { ADMIN_PASSWORD, ADMIN_USERNAME, login, settle } from "./helpers"

/**
 * T28 运行截图：把真实在跑的页面逐页存进 docs/screenshots。
 *
 * 这些图是「系统真的跑起来」的证据，不是设计稿。
 * 运行：npm run e2e:shots（前提是 windows-deploy\start.bat 已经在跑）。
 */
const SHOT_DIR = process.env.E2E_SHOT_DIR
  ? path.resolve(process.env.E2E_SHOT_DIR)
  : path.resolve(process.cwd(), "..", "docs", "screenshots")

mkdirSync(SHOT_DIR, { recursive: true })

async function shoot(page: Page, name: string): Promise<void> {
  await settle(page)
  await page.screenshot({ path: path.join(SHOT_DIR, `${name}.png`), fullPage: true })
}

test("00 登录页", async ({ page }) => {
  await page.goto("/login")
  await shoot(page, "00-登录")
})

test("01 驾驶舱与全球宏观", async ({ page }) => {
  await login(page)
  await page.goto("/dashboard")
  await shoot(page, "01-总览驾驶舱")

  await page.goto("/macro/us")
  await shoot(page, "02-美国宏观")

  await page.goto("/macro/china")
  await shoot(page, "03-中国宏观")

  await page.goto("/macro/liquidity")
  await shoot(page, "04-货币流动性")

  await page.goto("/macro/scenarios")
  await shoot(page, "05-宏观情景分析")
})

test("02 市场行情", async ({ page }) => {
  await login(page)

  await page.goto("/market/indices")
  await shoot(page, "06-指数行情")

  await page.goto("/market/sectors")
  await shoot(page, "07-板块排行")

  await page.goto("/market/stocks/600519.SH")
  await shoot(page, "08-个股K线")

  await page.goto("/market/temperature")
  await shoot(page, "09-市场温度")
})

test("03 公司与产业", async ({ page }) => {
  await login(page)

  await page.goto("/company/AAPL")
  await shoot(page, "10-公司研究")

  // A 股公司单拍一张：它有真实入库日线，美股那张走的是 SEC 事实路径。
  await page.goto("/company/600519.SH")
  await shoot(page, "10b-公司研究-贵州茅台")

  await page.goto("/company/AAPL/filings")
  await shoot(page, "11-财报资料")

  await page.goto("/industry")
  await shoot(page, "12-产业研究")

  const first = await page.evaluate(async () => {
    const response = await fetch("/api/v1/industries")
    const body = (await response.json()) as { data?: { industries?: { id?: string }[] } }
    return body.data?.industries?.[0]?.id ?? null
  })
  if (first) {
    await page.goto(`/industry/${encodeURIComponent(first)}`)
    await shoot(page, "13-产业详情")
  }
})

test("04 热点、问答、报告、自选", async ({ page }) => {
  await login(page)

  await page.goto("/hotspots")
  await shoot(page, "14-市场热点")

  await page.goto("/assistant")
  await shoot(page, "15-AI投研助手")

  await page.goto("/reports")
  await shoot(page, "16-研究报告")

  await page.goto("/watchlist")
  await shoot(page, "17-自选关注")

  await page.goto("/research/board")
  await shoot(page, "18-研究板")

  await page.goto("/simulation/mvp")
  await shoot(page, "19-研究推演")
})

test("05 数据管理、设置、运行状态", async ({ page }) => {
  await login(page)

  await page.goto("/data-center")
  await shoot(page, "20-数据管理")

  await page.goto("/settings")
  await shoot(page, "21-系统设置")

  await page.goto("/status")
  await shoot(page, "22-运行状态")
})

test("06 带证据的报告详情", async ({ page }) => {
  await login(page)

  // 用接口真造一份报告，再截图详情页，保证图里有真实引用而不是空壳。
  const created = await page.evaluate(
    async ([username, password]) => {
      const form = new URLSearchParams({ username, password })
      const loginResponse = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      })
      void form
      const loginBody = (await loginResponse.json()) as { data?: { access_token?: string } }
      const token = loginBody.data?.access_token
      if (!token) {
        return null
      }
      const search = await fetch("/api/v1/instruments/search?q=AAPL")
      const searchBody = (await search.json()) as { data?: { results?: { id?: string }[] } }
      const instrumentId = searchBody.data?.results?.[0]?.id
      if (!instrumentId) {
        return null
      }
      const periods = await fetch(
        `/api/v1/companies/${instrumentId}/research?as_of=2024-06-01`,
      )
      const periodsBody = (await periods.json()) as {
        data?: { available_periods?: string[] }
      }
      const available = periodsBody.data?.available_periods ?? []
      if (available.length < 2) {
        return null
      }
      const runResponse = await fetch("/api/v1/analysis/filing", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          instrument_id: instrumentId,
          as_of: "2024-06-01",
          period_a: available[0],
          period_b: available[available.length - 1],
        }),
      })
      if (!runResponse.ok) {
        return null
      }
      const runBody = (await runResponse.json()) as { data?: { run_id?: string } }
      return runBody.data?.run_id ?? null
    },
    [ADMIN_USERNAME, ADMIN_PASSWORD] as const,
  )

  expect(created, "需要先能生成一份报告").toBeTruthy()
  await page.goto(`/reports/${created}`)
  await shoot(page, "23-报告详情与证据")
})
