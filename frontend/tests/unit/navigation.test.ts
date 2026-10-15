import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it } from "vitest"
import { installGuard, routes, decideNavigation } from "../../src/router"
import { clearSession, safeRedirect, session } from "../../src/stores/auth"

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
    session.accessToken = "token"
    session.role = "researcher"
    await router.push("/data-center")
    expect(router.currentRoute.value.name).toBe("forbidden")
  })

  it("管理员可以进入管理页", async () => {
    const router = createRouter({ history: createMemoryHistory(), routes })
    installGuard(router, async () => false)
    session.accessToken = "token"
    session.role = "admin"
    await router.push("/data-center")
    expect(router.currentRoute.value.name).toBe("data-center")
  })

  it("公开运行状态页不要求登录", () => {
    expect(decideNavigation({ fullPath: "/", meta: {} }, { authenticated: false, role: null })).toBe(true)
  })

  it("只接受站内回跳地址", () => {
    expect(safeRedirect("/settings")).toBe("/settings")
    expect(safeRedirect("https://evil.example")).toBe("/settings")
    expect(safeRedirect("//evil.example")).toBe("/settings")
  })
})
