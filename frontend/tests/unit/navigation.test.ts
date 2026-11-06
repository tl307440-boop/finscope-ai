import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it } from "vitest"
import { installGuard, routes, decideNavigation } from "../../src/router"
import { clearSession, safeRedirect, useSessionStore } from "../../src/stores/auth"

afterEach(() => {
  clearSession()
})

describe("路由守卫", () => {
  it("未登录访问账号页时跳到登录页", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    installGuard(router, async () => false)
    await router.push("/settings")
    expect(router.currentRoute.value.name).toBe("login")
    expect(router.currentRoute.value.query.redirect).toBe("/settings")
  })

  it("研究者不能进入管理页", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    installGuard(router, async () => false)
    const session = useSessionStore()
    session.accessToken = "token"
    session.role = "researcher"
    await router.push("/data-center")
    expect(router.currentRoute.value.name).toBe("forbidden")
  })

  it("管理员可以进入管理页", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    installGuard(router, async () => false)
    const session = useSessionStore()
    session.accessToken = "token"
    session.role = "admin"
    await router.push("/data-center")
    expect(router.currentRoute.value.name).toBe("data-center")
  })

  it("市场行情的四个模块都已注册，且不要求登录", () => {
    // 只检查路由表，不真正挂载页面（页面依赖图表库，加载很慢）
    const router = createRouter({ history: createMemoryHistory(), routes })
    for (const [path, name] of [
      ["/market/indices", "market-indices"],
      ["/market/dragon-tiger", "market-dragon-tiger"],
      ["/market/ladder", "market-ladder"],
      ["/market/news", "market-news"],
    ] as const) {
      const resolved = router.resolve(path)
      expect(resolved.name).toBe(name)
      expect(resolved.meta.requiresAuth).toBeFalsy()
    }
  })

  it("旧的热点页地址重定向到热点消息", () => {
    const record = routes.find((r) => r.path === "/hotspots")
    expect(record?.redirect).toBe("/market/news")
  })

  it("公开运行状态页不要求登录", () => {
    expect(decideNavigation({ fullPath: "/", meta: {} }, { authenticated: false, role: null })).toBe(true)
  })

  it("只接受站内回跳地址，非法地址落到总览驾驶舱", () => {
    expect(safeRedirect("/settings")).toBe("/settings")
    expect(safeRedirect("/company/600519.SH")).toBe("/company/600519.SH")
    expect(safeRedirect("https://evil.example")).toBe("/dashboard")
    expect(safeRedirect("//evil.example")).toBe("/dashboard")
    expect(safeRedirect(undefined)).toBe("/dashboard")
  })
})
