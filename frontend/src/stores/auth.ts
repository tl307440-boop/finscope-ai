import { defineStore } from "pinia"
import { apiUrl, errorFrom, readJson } from "../api/client"

export type Role = "researcher" | "admin"

/**
 * 会话状态放在 Pinia store 中（规格书 1.2「组件与状态：Element Plus、Pinia、Vue Router」）。
 *
 * 只保存内存中的访问令牌：刷新凭据是 HttpOnly cookie，浏览器刷新页面后由
 * restoreSession() 调 /auth/refresh 重新换回访问令牌，因此令牌不落 localStorage。
 */
export const useSessionStore = defineStore("session", {
  state: () => ({
    accessToken: null as string | null,
    role: null as Role | null,
    username: null as string | null,
  }),
  getters: {
    authenticated: (state): boolean => state.accessToken !== null,
  },
  actions: {
    clear(): void {
      this.accessToken = null
      this.role = null
      this.username = null
    },
    /** 校验并写入登录/刷新接口返回的 body；结构不符时抛出后端错误。 */
    apply(body: unknown): void {
      const data = asRecord(asRecord(body)?.data)
      const token = asString(data?.access_token)
      const user = asRecord(data?.user)
      const role = user?.role
      if (!token || (role !== "researcher" && role !== "admin")) {
        throw errorFrom(body, null)
      }
      this.accessToken = token
      this.role = role
      this.username = asString(user?.username)
    },
  },
})

export function safeRedirect(value: unknown): string {
  if (typeof value === "string" && value.startsWith("/") && !value.startsWith("//")) {
    return value
  }
  // 没有回跳地址时落到总览驾驶舱：直接打开 /login 登录的默认落点，
  // 不该是系统设置页（规格书 §1.3 把 /dashboard 定为 P0 首页）。
  return "/dashboard"
}

export function clearSession(): void {
  useSessionStore().clear()
}

export function applySession(body: unknown): void {
  useSessionStore().apply(body)
}

export async function login(
  username: string,
  password: string,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const response = await fetcher(apiUrl("/api/v1/auth/login"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  useSessionStore().apply(body)
}

export async function restoreSession(fetcher: typeof fetch = fetch): Promise<boolean> {
  const session = useSessionStore()
  if (session.accessToken) {
    return true
  }
  const response = await fetcher(apiUrl("/api/v1/auth/refresh"), {
    method: "POST",
    credentials: "include",
  })
  if (!response.ok) {
    session.clear()
    return false
  }
  try {
    session.apply(await readJson(response))
  } catch {
    session.clear()
    return false
  }
  return true
}

export async function logout(fetcher: typeof fetch = fetch): Promise<void> {
  await fetcher(apiUrl("/api/v1/auth/logout"), { method: "POST", credentials: "include" })
  useSessionStore().clear()
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}
