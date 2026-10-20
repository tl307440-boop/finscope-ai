import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue"
import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it, vi } from "vitest"
import { routes } from "../../src/router"
import { clearSession } from "../../src/stores/auth"
import LoginView from "../../src/views/LoginView.vue"

const loginBody = {
  data: {
    access_token: "access-token",
    token_type: "bearer",
    user: {
      id: "5b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b",
      username: "researcher",
      email: "researcher@example.com",
      role: "researcher",
    },
  },
}

const profileBody = {
  data: {
    user: loginBody.data.user,
    preferences: {
      theme: "dark",
      rise_color_scheme: "cn",
      timezone: "Asia/Shanghai",
      default_market: "CN",
      default_lookback: "1y",
    },
  },
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

afterEach(() => {
  cleanup()
  clearSession()
  vi.unstubAllGlobals()
})

// 这里只验证「登录成功后跳到总览」。路由表是懒加载的，真去加载总览页会连带转译 ECharts 等一大串依赖，
// 既慢又与登录页无关，所以把总览换成空占位组件，路由名与路径保持不变。
const testRoutes = routes.map((route) =>
  route.name === "dashboard" ? { ...route, component: { render: () => null } } : route,
)

async function renderLogin() {
  const router = createRouter({ history: createMemoryHistory(), routes: testRoutes })
  await router.push("/login")
  await router.isReady()
  render(LoginView, { global: { plugins: [router] } })
  return router
}

describe("登录页", () => {
  it("空表单不发送请求", async () => {
    const fetcher = vi.fn()
    vi.stubGlobal("fetch", fetcher)
    await renderLogin()
    await fireEvent.click(screen.getByRole("button", { name: "登录" }))
    expect(screen.getByRole("alert").textContent).toContain("请输入账号和密码")
    const logins = fetcher.mock.calls.filter(([input]) => String(input).endsWith("/auth/login"))
    expect(logins).toHaveLength(0)
  })

  it("注册未开放时不显示注册入口", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ data: { registration_open: false } })))
    await renderLogin()
    expect(await screen.findByText("当前不开放注册，请联系管理员创建账号。")).toBeTruthy()
    expect(screen.queryByRole("button", { name: "注册新账号" })).toBeNull()
  })

  it("开放注册时可切换到注册表单，校验后提交并进入驾驶舱", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith("/auth/options")) {
        return json({ data: { registration_open: true } })
      }
      if (url.endsWith("/auth/register")) {
        return json(loginBody, 201)
      }
      return json(profileBody)
    })
    vi.stubGlobal("fetch", fetcher)
    const router = await renderLogin()
    await fireEvent.click(await screen.findByRole("button", { name: "注册新账号" }))
    await fireEvent.update(screen.getByLabelText("账号"), "newuser")
    await fireEvent.update(screen.getByLabelText("邮箱"), "new@example.com")
    await fireEvent.update(screen.getByLabelText("密码"), "correct-horse-9")
    await fireEvent.update(screen.getByLabelText("确认密码"), "different-pass-1")
    await fireEvent.click(screen.getByRole("button", { name: "注册并登录" }))
    expect(screen.getByRole("alert").textContent).toContain("两次输入的密码不一致")
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/auth/register"))).toBe(false)

    await fireEvent.update(screen.getByLabelText("密码"), "correct-horse-9")
    await fireEvent.update(screen.getByLabelText("确认密码"), "correct-horse-9")
    await fireEvent.click(screen.getByRole("button", { name: "注册并登录" }))
    await waitFor(() => {
      expect(router.currentRoute.value.name).toBe("dashboard")
    })
    const call = fetcher.mock.calls.find(([input]) => String(input).endsWith("/auth/register"))
    expect(JSON.parse(String((call?.[1] as RequestInit).body))).toEqual({
      username: "newuser",
      email: "new@example.com",
      password: "correct-horse-9",
    })
  })

  it("错误密码只显示统一提示，并清掉密码框", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json(
          { error: { code: "ACCOUNT_INVALID", message: "账号或密码错误", details: {} }, request_id: "req" },
          401,
        ),
      ),
    )
    await renderLogin()
    await fireEvent.update(screen.getByLabelText("账号"), "researcher")
    await fireEvent.update(screen.getByLabelText("密码"), "super-secret-pass")
    await fireEvent.click(screen.getByRole("button", { name: "登录" }))
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "账号或密码错误")
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("")
    expect(screen.queryByText("super-secret-pass")).toBeNull()
  })

  it("登录成功后进入总览驾驶舱", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).endsWith("/auth/login")) {
          return json(loginBody)
        }
        return json(profileBody)
      }),
    )
    const router = await renderLogin()
    await fireEvent.update(screen.getByLabelText("账号"), "researcher")
    await fireEvent.update(screen.getByLabelText("密码"), "correct-password")
    await fireEvent.click(screen.getByRole("button", { name: "登录" }))
    await waitFor(() => {
      expect(router.currentRoute.value.name).toBe("dashboard")
    })
    expect(screen.queryByText("correct-password")).toBeNull()
  })
})
