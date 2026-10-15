import { expect, type Page } from "@playwright/test"

/** 开发库管理员账号（windows-deploy\start.ps1 里 seed 出来的）。 */
export const ADMIN_USERNAME = process.env.E2E_USERNAME ?? "admin"
export const ADMIN_PASSWORD = process.env.E2E_PASSWORD ?? "FsScope!2026"

/** 走真实登录页登录。会话令牌只存在内存里，刷新靠 HttpOnly cookie。 */
export async function login(page: Page): Promise<void> {
  await page.goto("/login")
  await page.locator('input[name="username"]').fill(ADMIN_USERNAME)
  await page.locator('input[name="password"]').fill(ADMIN_PASSWORD)
  await page.locator('button[type="submit"]').click()
  await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 })
}

/** 等到页面既没有骨架屏、也不是加载中。 */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle")
  await expect(page.locator(".skeleton")).toHaveCount(0, { timeout: 20_000 })
}
