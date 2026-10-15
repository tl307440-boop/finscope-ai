import { cleanup, render, screen } from "@testing-library/vue"
import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it, vi } from "vitest"
import { routes } from "../../src/router"
import { clearSession, useSessionStore } from "../../src/stores/auth"
import SettingsView from "../../src/views/SettingsView.vue"

function profile(role: "admin" | "researcher") {
  return {
    data: {
      user: {
        id: "5b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b",
        username: role,
        email: `${role}@example.com`,
        role,
      },
      preferences: {
        theme: "dark",
        rise_color_scheme: "cn",
        timezone: "Asia/Shanghai",
        default_market: "CN",
        default_lookback: "1y",
      },
    },
  }
}

afterEach(() => {
  cleanup()
  clearSession()
  vi.unstubAllGlobals()
})

async function renderSettings(role: "admin" | "researcher"): Promise<void> {
  const session = useSessionStore()
  session.accessToken = "token"
  session.role = role
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(profile(role)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push("/settings")
  await router.isReady()
  render(SettingsView, { global: { plugins: [router] } })
}

describe("账号设置入口", () => {
  it("管理员能看到数据源管理入口", async () => {
    await renderSettings("admin")
    const link = await screen.findByRole("link", { name: "打开数据源管理" })
    expect(link.getAttribute("href")).toBe("/data-center")
  })

  it("研究者看不到数据源管理入口", async () => {
    await renderSettings("researcher")
    await screen.findByRole("button", { name: "退出" })
    expect(screen.queryByRole("link", { name: "打开数据源管理" })).toBeNull()
  })
})
