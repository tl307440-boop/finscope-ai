/** 韭研公社扫码登录（仅管理员）：取二维码、查进度。会话由后端加密保存，前端拿不到会话值。 */

import { authorizedFetch, errorFrom, readJson } from "./client"
import { asNumber, asRecord, asString, dataOf } from "./json"

export type JygsLoginStatus =
  | "waiting"
  | "scanned"
  | "confirmed"
  | "expired"
  | "cancelled"
  | "failed"

export interface JygsLoginStart {
  loginId: string
  /** data URL，直接放进 <img>。 */
  qrImage: string
  expiresInSeconds: number
}

export interface JygsLoginProgress {
  status: JygsLoginStatus
  message: string
}

type Refresh = () => Promise<string | null>

const BASE = "/api/v1/admin/jygs-login"
const STATUSES: readonly JygsLoginStatus[] = [
  "waiting",
  "scanned",
  "confirmed",
  "expired",
  "cancelled",
  "failed",
]

async function call(
  path: string,
  init: RequestInit,
  token: string | null,
  refresh: Refresh,
): Promise<Record<string, unknown>> {
  const response = await authorizedFetch(path, init, token, refresh)
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return dataOf(body) ?? asRecord(body) ?? {}
}

export async function startJygsLogin(
  token: string | null,
  refresh: Refresh,
): Promise<JygsLoginStart> {
  const data = await call(BASE, { method: "POST" }, token, refresh)
  return {
    loginId: asString(data.loginId) ?? "",
    qrImage: asString(data.qrImage) ?? "",
    expiresInSeconds: asNumber(data.expiresInSeconds) ?? 150,
  }
}

export async function pollJygsLogin(
  loginId: string,
  token: string | null,
  refresh: Refresh,
): Promise<JygsLoginProgress> {
  const data = await call(`${BASE}/${encodeURIComponent(loginId)}`, { method: "GET" }, token, refresh)
  const raw = asString(data.status)
  const status = STATUSES.find((s) => s === raw) ?? "failed"
  return { status, message: asString(data.message) ?? "" }
}
