/**
 * 东方财富账号绑定（/api/v1/eastmoney）：扫码登录后，把东财自选单向同步成本系统的自选分组。
 *
 *  - GET    /eastmoney            绑定状态
 *  - POST   /eastmoney/qr         生成扫码二维码（PNG，base64）
 *  - GET    /eastmoney/qr/{id}    查询扫码状态；扫码成功后后端立刻同步一次
 *  - POST   /eastmoney/sync       立即同步（后端 30 秒内合并重复请求）
 *  - DELETE /eastmoney            解除绑定（后端立即删除登录凭证，已同步的自选保留）
 *
 * 登录凭证只在后端加密保存，前端接口里没有任何 cookie 或令牌。
 */
import { authorizedFetch, readJson } from "./client"
import { FetchFailure, unwrap } from "./industryMarket"
import { asList, asNumber, asString } from "./json"

export type Refresh = () => Promise<string | null>

export interface BindingStatus {
  bound: boolean
  /** active 正常；expired 登录已失效，需要重新扫码。 */
  status: "active" | "expired" | ""
  alias: string
  boundAt: string
  lastSyncAt: string
  message: string
  groups: number | null
  items: number | null
}

export interface SyncResult {
  groups: number
  items: number
  added: number
  removed: number
  message: string
  syncedAt: string
  skippedGroups: string[]
  /** 刚同步过，后端只返回了上次结果，没有再请求东财。 */
  cached: boolean
}

export interface QrCode {
  loginId: string
  /** 可直接放进 img.src 的 data URL。 */
  imageSrc: string
  expiresIn: number
}

export type QrPhase = "waiting" | "expired" | "success"

export interface QrState {
  phase: QrPhase
  alias: string
  sync: SyncResult | null
  syncError: string
}

async function authed(path: string, init: RequestInit, token: string | null, refresh: Refresh) {
  let response: Response
  try {
    response = await authorizedFetch(path, init, token, refresh)
  } catch {
    throw new FetchFailure("网络连接失败，请稍后重试")
  }
  return unwrap(response, await readJson(response))
}

export function parseBinding(data: Record<string, unknown>): BindingStatus {
  const status = asString(data.status)
  return {
    bound: data.bound === true,
    status: status === "active" || status === "expired" ? status : "",
    alias: asString(data.alias) ?? "",
    boundAt: asString(data.bound_at) ?? "",
    lastSyncAt: asString(data.last_sync_at) ?? "",
    message: asString(data.message) ?? "",
    groups: asNumber(data.groups),
    items: asNumber(data.items),
  }
}

export function parseSync(data: unknown): SyncResult | null {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return null
  }
  const row = data as Record<string, unknown>
  return {
    groups: asNumber(row.groups) ?? 0,
    items: asNumber(row.items) ?? 0,
    added: asNumber(row.added) ?? 0,
    removed: asNumber(row.removed) ?? 0,
    message: asString(row.message) ?? "",
    syncedAt: asString(row.synced_at) ?? "",
    skippedGroups: asList(row.skipped_groups).flatMap((item) => (typeof item === "string" ? [item] : [])),
    cached: row.cached === true,
  }
}

export function parseQrState(data: Record<string, unknown>): QrState {
  const state = asString(data.state)
  return {
    phase: state === "success" || state === "expired" ? state : "waiting",
    alias: asString(data.alias) ?? "",
    sync: parseSync(data.sync),
    syncError: asString(data.sync_error) ?? "",
  }
}

export async function fetchBinding(token: string | null, refresh: Refresh): Promise<BindingStatus> {
  return parseBinding(await authed("/api/v1/eastmoney", { method: "GET" }, token, refresh))
}

export async function startQr(token: string | null, refresh: Refresh): Promise<QrCode> {
  const data = await authed("/api/v1/eastmoney/qr", { method: "POST" }, token, refresh)
  const loginId = asString(data.login_id)
  const png = asString(data.qr_png_base64)
  if (!loginId || !png) {
    throw new FetchFailure("二维码生成失败，请重试")
  }
  return { loginId, imageSrc: `data:image/png;base64,${png}`, expiresIn: asNumber(data.expires_in) ?? 120 }
}

export async function pollQr(loginId: string, token: string | null, refresh: Refresh): Promise<QrState> {
  const path = `/api/v1/eastmoney/qr/${encodeURIComponent(loginId)}`
  return parseQrState(await authed(path, { method: "GET" }, token, refresh))
}

export async function syncNow(token: string | null, refresh: Refresh): Promise<SyncResult> {
  const data = await authed("/api/v1/eastmoney/sync", { method: "POST" }, token, refresh)
  const parsed = parseSync(data)
  if (!parsed) {
    throw new FetchFailure("接口返回了无法识别的内容")
  }
  return parsed
}

export async function unbind(token: string | null, refresh: Refresh): Promise<void> {
  await authed("/api/v1/eastmoney", { method: "DELETE" }, token, refresh)
}
