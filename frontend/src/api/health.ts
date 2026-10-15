import { ApiError, apiUrl, readJson } from "./client"

export interface ComponentCheck {
  status: "up" | "down"
  reason?: string
  latencyMs?: number
  lastSeenAt?: string
}

export interface SystemSnapshot {
  dataMode: "live" | "demo"
  notice: string | null
  requestId: string
  asOf: string
  ready: boolean
  database: ComponentCheck
  worker: ComponentCheck
}

const REASON_TEXT: Record<string, string> = {
  heartbeat_missing: "尚未上报心跳",
  heartbeat_stale: "心跳过期",
  heartbeat_stopped: "已停止",
  heartbeat_invalid: "心跳无效",
  heartbeat_unreadable: "心跳无法读取",
  database_unavailable: "数据库不可用",
}

export function reasonText(reason: string | undefined): string | null {
  if (!reason) {
    return null
  }
  return REASON_TEXT[reason] ?? "状态未知"
}

export function formatShanghai(iso: string | undefined): string {
  if (!iso) {
    return "—"
  }
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return "—"
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(date)
}

export function docsUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "")
  return configured ? `${configured}/docs` : "/docs"
}

export function parseSystemStatus(
  liveBody: unknown,
  readyStatus: number,
  readyBody: unknown,
): SystemSnapshot {
  const live = asRecord(liveBody)
  const meta = asRecord(live?.meta)
  const requestId = asString(meta?.request_id)
  const asOf = asString(meta?.as_of)
  const dataMode = meta?.data_mode
  if (!requestId || !asOf || (dataMode !== "live" && dataMode !== "demo")) {
    throw new ApiError("存活检查响应无法识别", "INVALID_RESPONSE", requestId)
  }
  const ready = readyStatus === 200 ? asRecord(asRecord(readyBody)?.data) : null
  const failed = readyStatus === 503 ? asRecord(asRecord(asRecord(readyBody)?.error)?.details) : null
  const checks = asRecord(ready?.checks) ?? asRecord(failed?.checks)
  const database = parseCheck(checks?.database)
  const worker = parseCheck(checks?.worker)
  if (!database || !worker || (readyStatus !== 200 && readyStatus !== 503)) {
    throw new ApiError("就绪检查响应无法识别", "INVALID_RESPONSE", requestId)
  }
  return {
    dataMode,
    notice: asString(meta?.notice),
    requestId,
    asOf,
    ready: readyStatus === 200,
    database,
    worker,
  }
}

export async function fetchSystemStatus(fetcher: typeof fetch = fetch): Promise<SystemSnapshot> {
  const liveResponse = await fetcher(apiUrl("/api/v1/health/live"))
  const liveBody = await readJson(liveResponse)
  if (!liveResponse.ok) {
    throw errorFrom(liveBody, liveResponse.headers.get("X-Request-ID"))
  }
  const readyResponse = await fetcher(apiUrl("/api/v1/health/ready"))
  const readyBody = await readJson(readyResponse)
  if (readyResponse.ok || readyResponse.status === 503) {
    return parseSystemStatus(liveBody, readyResponse.status, readyBody)
  }
  throw errorFrom(readyBody, readyResponse.headers.get("X-Request-ID"))
}

function errorFrom(body: unknown, headerRequestId: string | null): ApiError {
  const record = asRecord(body)
  const error = asRecord(record?.error)
  const message = asString(error?.message) ?? "请求失败"
  const code = asString(error?.code) ?? "HTTP_ERROR"
  const requestId = asString(record?.request_id) ?? headerRequestId
  return new ApiError(message, code, requestId)
}

function parseCheck(value: unknown): ComponentCheck | null {
  const record = asRecord(value)
  const status = record?.status
  if (status !== "up" && status !== "down") {
    return null
  }
  const latency = record?.latency_ms
  return {
    status,
    reason: asString(record?.reason) ?? undefined,
    latencyMs: typeof latency === "number" ? latency : undefined,
    lastSeenAt: asString(record?.last_seen_at) ?? undefined,
  }
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
