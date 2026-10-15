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
