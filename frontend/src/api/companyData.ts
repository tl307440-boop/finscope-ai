/**
 * 数据就绪度接口：某家公司的财报 / 日线有没有、新不新、是否正在后台抓取。
 *
 *  - GET  /api/v1/companies/{id}/data-status    公开，只读
 *  - POST /api/v1/companies/{id}/data-requests  需登录；缺数据或已过期时登记后台抓取（后端按冷却与全局上限限流）
 *  - GET  /api/v1/system/freshness              各数据源的新鲜度与进程心跳
 */
import { apiUrl, authorizedFetch, errorFrom, readJson } from "./client"
import { asString, dataOf, listOf } from "./json"

export type DataState = "fresh" | "warn" | "stale" | "missing" | "unavailable"
export type SyncState = "idle" | "queued" | "running" | "failed"

export interface DatasetStatus {
  dataset: string
  label: string
  source: string
  state: DataState
  sync: SyncState
  latestDataDate: string | null
  lastSyncedAt: string | null
  message: string
  canRequest: boolean
}

export interface CompanyDataStatus {
  instrumentId: string
  generatedAt: string
  syncing: boolean
  datasets: DatasetStatus[]
}

export interface FreshnessItem {
  group: string
  key: string
  label: string
  state: DataState
  detail: string
  latestDataDate: string | null
  lastSyncedAt: string | null
}

export interface FreshnessReport {
  generatedAt: string
  schedulerRunning: boolean
  items: FreshnessItem[]
}

const STATES: readonly DataState[] = ["fresh", "warn", "stale", "missing", "unavailable"]
const SYNCS: readonly SyncState[] = ["idle", "queued", "running", "failed"]

function stateOf(value: unknown): DataState {
  const text = asString(value)
  return STATES.find((item) => item === text) ?? "missing"
}

function syncOf(value: unknown): SyncState {
  const text = asString(value)
  return SYNCS.find((item) => item === text) ?? "idle"
}

export function parseDataStatus(body: unknown): CompanyDataStatus | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  return {
    instrumentId: asString(data.instrument_id) ?? "",
    generatedAt: asString(data.generated_at) ?? "",
    syncing: data.syncing === true,
    datasets: listOf(data.datasets).map((row) => ({
      dataset: asString(row.dataset) ?? "",
      label: asString(row.label) ?? "",
      source: asString(row.source) ?? "",
      state: stateOf(row.state),
      sync: syncOf(row.sync),
      latestDataDate: asString(row.latest_data_date),
      lastSyncedAt: asString(row.last_synced_at),
      message: asString(row.message) ?? "",
      canRequest: row.can_request === true,
    })),
  }
}

export function parseFreshness(body: unknown): FreshnessReport | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  return {
    generatedAt: asString(data.generated_at) ?? "",
    schedulerRunning: data.scheduler_running === true,
    items: listOf(data.items).map((row) => ({
      group: asString(row.group) ?? "",
      key: asString(row.key) ?? "",
      label: asString(row.label) ?? "",
      state: stateOf(row.state),
      detail: asString(row.detail) ?? "",
      latestDataDate: asString(row.latest_data_date),
      lastSyncedAt: asString(row.last_synced_at),
    })),
  }
}

export async function fetchDataStatus(instrumentId: string): Promise<CompanyDataStatus | null> {
  const response = await fetch(apiUrl(`/api/v1/companies/${instrumentId}/data-status`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseDataStatus(body)
}

/** 登记后台抓取并返回最新状态；调用方只在已登录时调用。 */
export async function requestDataSync(
  instrumentId: string,
  token: string | null,
  refresh: () => Promise<string | null>,
): Promise<CompanyDataStatus | null> {
  const response = await authorizedFetch(
    `/api/v1/companies/${instrumentId}/data-requests`,
    { method: "POST" },
    token,
    refresh,
  )
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseDataStatus(body)
}

export async function fetchFreshness(): Promise<FreshnessReport | null> {
  const response = await fetch(apiUrl("/api/v1/system/freshness"))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseFreshness(body)
}

export const STATE_LABELS: Record<DataState, string> = {
  fresh: "最新",
  warn: "略有延迟",
  stale: "已过期",
  missing: "尚无数据",
  unavailable: "不可用",
}

export function stateTone(state: DataState): "ok" | "warn" | "danger" | "muted" {
  if (state === "fresh") {
    return "ok"
  }
  if (state === "warn") {
    return "warn"
  }
  return state === "stale" ? "danger" : "muted"
}
