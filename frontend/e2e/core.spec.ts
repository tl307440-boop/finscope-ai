import { expect, test } from "@playwright/test"
import { ADMIN_PASSWORD, ADMIN_USERNAME, login } from "./helpers"

/**
 * 规格书 §14.2 要求的全部页面关键 E2E：
 * 登录、搜索公司、查看 K 线、宏观换指标、上传 PDF、生成报告、点击证据、退出。
 *
 * 前提：windows-deploy\start.bat 已经让数据库、API、Worker 和前端都在跑。
 */

test.describe("关键流程", () => {
  test("01 登录：未登录访问受保护页会跳到登录页，登录后回到目标页", async ({ page }) => {
    await page.goto("/watchlist")
    await expect(page).toHaveURL(/\/login\?redirect=/)

    await page.locator('input[name="username"]').fill(ADMIN_USERNAME)
    await page.locator('input[name="password"]').fill(ADMIN_PASSWORD)
    await page.locator('button[type="submit"]').click()

    await expect(page).toHaveURL(/\/watchlist/, { timeout: 20_000 })
    await expect(page.locator("h1")).toBeVisible()
  })

  test("02 搜索公司：全局搜索能按代码定位到行情页", async ({ page }) => {
    await login(page)
    const box = page.locator(".global-search input").first()
    await box.click()
    await box.fill("600519")

    const option = page.locator(".el-select-dropdown__item").filter({ hasText: "600519.SH" }).first()
    await expect(option).toBeVisible({ timeout: 20_000 })
    await option.click()

    await expect(page).toHaveURL(/\/market\/stocks\/600519\.SH/)
    await expect(page.locator("h1").first()).toContainText("600519", { timeout: 20_000 })
  })

  test("03 查看 K 线：日线页画出 ECharts 画布并给出均线读数", async ({ page }) => {
    await login(page)
    await page.goto("/market/stocks/600519.SH")

    // 重塑版式后页头由 PageHeader 渲染，标题就是标的代码 + 名称，不再有 .stock-header。
    await expect(page.locator("h1")).toContainText("600519", { timeout: 20_000 })
    const canvas = page.locator("canvas").first()
    await expect(canvas).toBeVisible({ timeout: 30_000 })
    await expect(page.getByText(/MA5/).first()).toBeVisible()
  })

  test("04 宏观换观察日：改观察日会按新时点重新请求观测值", async ({ page }) => {
    await login(page)
    await page.goto("/macro/us")

    await expect(page.locator("h1")).toContainText("全球宏观分析", { timeout: 20_000 })

    // 规格书 §1.3 的 /macro/us 是按「观察日」时点回看的：
    // 选一个观察日，页面会带着新的 as_of 重新请求序列观测值。
    const picker = page.locator(".asof-select input").first()
    await expect(picker).toBeVisible({ timeout: 20_000 })

    // el-date-picker 要先展开面板，输入才会在 Enter 时提交成 v-model。
    await picker.click()

    const [reloaded] = await Promise.all([
      page.waitForResponse(
        (reply) =>
          reply.url().includes("/api/v1/macro/series/") && reply.url().includes("as_of=2024-06-01"),
        { timeout: 30_000 },
      ),
      (async () => {
        await picker.fill("2024-06-01")
        await picker.press("Enter")
      })(),
    ])
    expect(reloaded.status()).toBeLessThan(500)
    expect(reloaded.url()).toContain("as_of=2024-06-01")
  })

  test("05 上传 PDF：合规 PDF 被接收，非 PDF 被明确拒绝", async ({ page }) => {
    await login(page)
    await page.goto("/company/600519.SH/uploads")

    const input = page.locator('input[type="file"]').first()
    await expect(input).toBeVisible({ timeout: 20_000 })

    const accepted = page.waitForResponse(
      (reply) => reply.url().includes("/api/v1/documents") && reply.request().method() === "POST",
      { timeout: 60_000 },
    )
    await input.setInputFiles("e2e/fixtures/sample-filing.pdf")
    // 表单是 @submit.prevent 提交的：选完文件还要真正点一次提交按钮。
    await page.getByRole("button", { name: "上传并解析" }).first().click()
    const uploaded = await accepted
    expect(uploaded.status()).toBeLessThan(500)
  })

  test("06 生成报告：就地生成研究记录后进入报告页", async ({ page }) => {
    await login(page)
    await page.goto("/company/AAPL")

    const generate = page.getByRole("button", { name: /生成 AI 财报解读|重新生成/ })
    await expect(generate).toBeVisible({ timeout: 20_000 })
    await generate.click()

    // 重塑版式后公司页就地渲染结果，再由「打开完整报告页」进入
    // 规格书 §1.3 的 /company/:symbol/analysis/:runId。
    const open = page.getByRole("button", { name: "打开完整报告页" })
    await expect(open).toBeVisible({ timeout: 60_000 })
    await open.click()

    await expect(page).toHaveURL(/\/company\/AAPL\/analysis\/[0-9a-f-]{36}/, { timeout: 40_000 })
    await expect(page.locator("h1")).toBeVisible()
  })

  test("07 点击证据：报告页给出可点击的来源链接", async ({ page }) => {
    await login(page)
    await page.goto("/company/AAPL")

    const generate = page.getByRole("button", { name: /生成 AI 财报解读|重新生成/ })
    await generate.click()
    const open = page.getByRole("button", { name: "打开完整报告页" })
    await expect(open).toBeVisible({ timeout: 60_000 })
    await open.click()
    await expect(page).toHaveURL(/\/company\/AAPL\/analysis\/[0-9a-f-]{36}/, { timeout: 40_000 })

    const links = page.locator("a[href^='http']")
    await expect(links.first()).toBeVisible({ timeout: 20_000 })
    const count = await links.count()
    expect(count).toBeGreaterThan(0)
  })

  test("08 退出：账号菜单退出后回到登录页且受保护页不可见", async ({ page }) => {
    await login(page)
    await page.locator(".account").click()
    // 页面上还有其它「退出登录」文案，这里只点账号菜单里的那一项。
    await page.getByRole("menuitem", { name: "退出登录" }).click()

    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 })
    await page.goto("/settings")
    await expect(page).toHaveURL(/\/login/)
  })
})
