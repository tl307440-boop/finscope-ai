const rawBase = import.meta.env.VITE_API_BASE_URL ?? ""

export function apiUrl(path: string): string {
  const base = rawBase.replace(/\/$/, "")
  return `${base}${path}`
}

export class ApiError extends Error {
  readonly code: string
  readonly requestId: string | null

  constructor(message: string, code: string, requestId: string | null) {
    super(message)
    this.name = "ApiError"
    this.code = code
    this.requestId = requestId
  }
}

export async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) {
    return null
  }
  try {
    return JSON.parse(text) as unknown
  } catch {
    throw new ApiError(
      "接口返回了无法解析的内容",
      "INVALID_RESPONSE",
      response.headers.get("X-Request-ID"),
    )
  }
}

export function errorFrom(body: unknown, headerRequestId: string | null): ApiError {
  const record = asRecord(body)
  const error = asRecord(record?.error)
  const message = asString(error?.message) ?? "请求失败"
  const code = asString(error?.code) ?? "HTTP_ERROR"
  const requestId = asString(record?.request_id) ?? headerRequestId
  return new ApiError(message, code, requestId)
}

export async function authorizedFetch(
  path: string,
  init: RequestInit,
  token: string | null,
  refresh: () => Promise<string | null>,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  const first = await send(path, init, token, fetcher)
  if (first.status !== 401 || path.endsWith("/auth/refresh") || path.endsWith("/auth/login")) {
    return first
  }
  const nextToken = await refresh()
  if (!nextToken) {
    return first
  }
  return send(path, init, nextToken, fetcher)
}

async function send(
  path: string,
  init: RequestInit,
  token: string | null,
  fetcher: typeof fetch,
): Promise<Response> {
  const headers = new Headers(init.headers)
  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  return fetcher(apiUrl(path), { ...init, headers, credentials: "include" })
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
