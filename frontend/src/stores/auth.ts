import { reactive } from "vue"
import { apiUrl, errorFrom, readJson } from "../api/client"

export type Role = "researcher" | "admin"

export const session = reactive({
  accessToken: null as string | null,
  role: null as Role | null,
  username: null as string | null,
})

export function safeRedirect(value: unknown): string {
  if (typeof value === "string" && value.startsWith("/") && !value.startsWith("//")) {
    return value
  }
  return "/settings"
}

export function clearSession(): void {
  session.accessToken = null
  session.role = null
  session.username = null
}

export function applySession(body: unknown): void {
  const data = asRecord(asRecord(body)?.data)
  const token = asString(data?.access_token)
  const user = asRecord(data?.user)
  const role = user?.role
  if (!token || (role !== "researcher" && role !== "admin")) {
    throw errorFrom(body, null)
  }
  session.accessToken = token
  session.role = role
  session.username = asString(user?.username)
}

export async function login(username: string, password: string, fetcher: typeof fetch = fetch): Promise<void> {
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
  applySession(body)
}

export async function restoreSession(fetcher: typeof fetch = fetch): Promise<boolean> {
  if (session.accessToken) {
    return true
  }
  const response = await fetcher(apiUrl("/api/v1/auth/refresh"), {
    method: "POST",
    credentials: "include",
  })
  if (!response.ok) {
    clearSession()
    return false
  }
  try {
    applySession(await readJson(response))
  } catch {
    clearSession()
    return false
  }
  return true
}

export async function logout(fetcher: typeof fetch = fetch): Promise<void> {
  await fetcher(apiUrl("/api/v1/auth/logout"), { method: "POST", credentials: "include" })
  clearSession()
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
