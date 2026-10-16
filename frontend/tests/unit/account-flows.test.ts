import { describe, expect, it, vi } from "vitest"
import { ApiError } from "../../src/api/client"
import {
  changePassword,
  fetchRegistrationOpen,
  passwordHint,
  register,
  useSessionStore,
} from "../../src/stores/auth"

function loginBody(token: string, role = "researcher"): string {
  return JSON.stringify({
    data: { access_token: token, token_type: "bearer", user: { id: "1", username: "ann", email: "a@b.co", role } },
  })
}

function errorBody(code: string, message: string): string {
  return JSON.stringify({ error: { code, message } })
}

describe("账号流程", () => {
  it("注册成功后直接进入登录状态，请求体包含账号邮箱密码", async () => {
    const fetcher = vi.fn(async () => new Response(loginBody("tok-1"), { status: 201 }))
    await register("ann", "a@b.co", "correct-horse-9", fetcher as unknown as typeof fetch)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain("/api/v1/auth/register")
    expect(JSON.parse(String(init.body))).toEqual({
      username: "ann",
      email: "a@b.co",
      password: "correct-horse-9",
    })
    expect(useSessionStore().accessToken).toBe("tok-1")
    expect(useSessionStore().role).toBe("researcher")
  })

  it("注册被拒时抛出后端给的原因，且不写入登录状态", async () => {
    useSessionStore().clear()
    const fetcher = vi.fn(
      async () => new Response(errorBody("ACCOUNT_EXISTS", "这个账号或邮箱已被使用"), { status: 409 }),
    )
    await expect(register("ann", "a@b.co", "correct-horse-9", fetcher as unknown as typeof fetch)).rejects.toThrow(
      ApiError,
    )
    expect(useSessionStore().accessToken).toBeNull()
  })

  it("注册开关：接口失败或关闭都按未开放处理", async () => {
    const open = vi.fn(async () => new Response(JSON.stringify({ data: { registration_open: true } }), { status: 200 }))
    expect(await fetchRegistrationOpen(open as unknown as typeof fetch)).toBe(true)
    const closed = vi.fn(async () => new Response(JSON.stringify({ data: { registration_open: false } }), { status: 200 }))
    expect(await fetchRegistrationOpen(closed as unknown as typeof fetch)).toBe(false)
    const broken = vi.fn(async () => {
      throw new Error("offline")
    })
    expect(await fetchRegistrationOpen(broken as unknown as typeof fetch)).toBe(false)
  })

  it("改密码成功后换上服务端签发的新令牌", async () => {
    const session = useSessionStore()
    session.accessToken = "old-token"
    const fetcher = vi.fn(async () => new Response(loginBody("new-token"), { status: 200 }))
    await changePassword("old-pass-1", "new-pass-22", fetcher as unknown as typeof fetch)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain("/api/v1/me/password")
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer old-token")
    expect(session.accessToken).toBe("new-token")
  })

  it("当前密码错误时保留原令牌并抛出原因", async () => {
    const session = useSessionStore()
    session.accessToken = "keep-me"
    const fetcher = vi.fn(async () => new Response(errorBody("ACCOUNT_INVALID", "当前密码不正确"), { status: 400 }))
    await expect(changePassword("bad", "new-pass-22", fetcher as unknown as typeof fetch)).rejects.toThrow("当前密码不正确")
    expect(session.accessToken).toBe("keep-me")
  })

  it("密码提示与后端策略一致", () => {
    expect(passwordHint("short1")).toBe("密码至少 8 位")
    expect(passwordHint("onlyletters")).toBe("密码需要同时包含字母和数字")
    expect(passwordHint("12345678")).toBe("密码需要同时包含字母和数字")
    expect(passwordHint("correct-horse-9")).toBeNull()
  })
})
