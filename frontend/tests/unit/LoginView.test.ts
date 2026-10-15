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

async function renderLogin() {
  const router = createRouter({ history: createMemoryHistory(), routes })
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
    expect(fetcher).not.toHaveBeenCalled()
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
