/**
 * 自定义数据分析（`/api/v1/custom-analysis/*`）。
 *
 * 所有数值（PCR、10 日均线、5 年分位、ERP、巴菲特指标、中位数 PB、信号）都由后端算好，
 * 这里只做运行时收窄和取数：缺失字段保持 null，永不补 0、不插值。
 * success_response 会省略 null 字段，所以每个字段都按「可能不存在」处理。
 */
import { ApiError, apiUrl, errorFrom, readJson } from "./client"
import { asList, asNumber, asRecord, asString, dataOf, listOf } from "./json"

export type DataStatus = "ok" | "stale" | "unavailable"

export const PCR_GROUPS = [
  { code: "sse50", name: "上证50" },
  { code: "csi300", name: "沪深300" },
  { code: "csi500", name: "中证500" },
  { code: "csi1000", name: "中证1000" },
] as const

export type PcrGroupCode = (typeof PCR_GROUPS)[number]["code"]

export interface PcrPoint {
  tradeDate: string
  indexClose: number | null
  callVolume: number
  putVolume: number
  callOi: number
  putOi: number
  volumePcr: number | null
  volumePcrMa10: number | null
  oiPcr: number | null
  percentile5y: number | null
  sentimentSignal: string | null
  relativeSignal: string | null
  qualityFlag: string
}

export interface PcrBasket {
  kind: "etf" | "index"
  label: string
  members: string[]
  note: string
  status: DataStatus
  reason: string | null
  firstDate: string | null
  lastDate: string | null
  historyYears: number | null
  incompleteDays: number
  latest: PcrPoint | null
  points: PcrPoint[]
}

export interface PcrView {
  groupCode: string
  groupName: string
  indexCode: string
  indexName: string
  baskets: PcrBasket[]
  rules: string[]
  rulesVersion: string
}

export type IndicatorCode = "erp" | "buffett" | "median_pb"

export interface IndicatorPoint {
  tradeDate: string
  value: number
  percentile5y: number | null
  bandMean: number | null
  bandUpper: number | null
  bandLower: number | null
  qualityFlag: string
  extras: Record<string, number | string | null>
}

export interface InputStatus {
  name: string
  source: string
  lastDate: string | null
  rows: number
}

export interface Indicator {
  code: IndicatorCode
  name: string
  unit: string
  definition: string
  status: DataStatus
  reason: string | null
  zone: string | null
  percentileNote: string | null
  latest: IndicatorPoint | null
  points: IndicatorPoint[]
  inputs: InputStatus[]
  notes: string[]
}

export interface ValuationView {
  indicators: Indicator[]
  rulesVersion: string
}

export interface SourceRow {
  key: string
  label: string
  provider: string
  url: string
  frequency: string
  status: DataStatus
  rows: number
  firstDate: string | null
  lastDate: string | null
  reviewRows: number
  fetchedAt: string | null
  note: string
}

export interface QualityIssue {
  tradeDate: string
  subject: string
  note: string
}

export interface SourcesView {
  sources: SourceRow[]
  recentIssues: QualityIssue[]
  rulesVersion: string
}

const INDICATOR_CODES: readonly string[] = ["erp", "buffett", "median_pb"]

function status(value: unknown): DataStatus {
  return value === "ok" || value === "stale" ? value : "unavailable"
}

function strings(value: unknown): string[] {
  return asList(value).flatMap((item) => {
    const text = asString(item)
    return text ? [text] : []
  })
}

export function parsePcrPoint(value: unknown): PcrPoint | null {
  const row = asRecord(value)
  const tradeDate = asString(row?.trade_date)
  if (!row || !tradeDate) {
    return null
  }
  return {
    tradeDate,
    indexClose: asNumber(row.index_close),
    callVolume: asNumber(row.call_volume) ?? 0,
    putVolume: asNumber(row.put_volume) ?? 0,
    callOi: asNumber(row.call_oi) ?? 0,
    putOi: asNumber(row.put_oi) ?? 0,
    volumePcr: asNumber(row.volume_pcr),
    volumePcrMa10: asNumber(row.volume_pcr_ma10),
    oiPcr: asNumber(row.oi_pcr),
    percentile5y: asNumber(row.volume_pcr_percentile_5y),
    sentimentSignal: asString(row.sentiment_signal),
    relativeSignal: asString(row.relative_signal),
    qualityFlag: asString(row.quality_flag) ?? "ok",
  }
}

function parseBasket(value: unknown): PcrBasket | null {
  const row = asRecord(value)
  const kind = row?.kind
  const label = asString(row?.label)
  if (!row || (kind !== "etf" && kind !== "index") || !label) {
    return null
  }
  const points = asList(row.points).flatMap((item) => {
    const point = parsePcrPoint(item)
    return point ? [point] : []
  })
  return {
    kind,
    label,
    members: strings(row.members),
    note: asString(row.note) ?? "",
    status: status(row.status),
    reason: asString(row.reason),
    firstDate: asString(row.first_date),
    lastDate: asString(row.last_date),
    historyYears: asNumber(row.history_years),
    incompleteDays: asNumber(row.incomplete_days) ?? 0,
    latest: parsePcrPoint(row.latest),
    points,
  }
}

export function parsePcr(body: unknown): PcrView | null {
  const data = dataOf(body)
  const groupCode = asString(data?.group_code)
  if (!data || !groupCode) {
    return null
  }
  return {
    groupCode,
    groupName: asString(data.group_name) ?? groupCode,
    indexCode: asString(data.index_code) ?? "",
    indexName: asString(data.index_name) ?? "",
    baskets: asList(data.baskets).flatMap((item) => {
      const basket = parseBasket(item)
      return basket ? [basket] : []
    }),
    rules: strings(data.rules),
    rulesVersion: asString(data.rules_version) ?? "",
  }
}

function parseExtras(value: unknown): Record<string, number | string | null> {
  const record = asRecord(value)
  const out: Record<string, number | string | null> = {}
  if (!record) {
    return out
  }
  for (const [key, item] of Object.entries(record)) {
    out[key] = typeof item === "number" || typeof item === "string" ? item : null
  }
  return out
}

function parseIndicatorPoint(value: unknown): IndicatorPoint | null {
  const row = asRecord(value)
  const tradeDate = asString(row?.trade_date)
  const point = asNumber(row?.value)
  if (!row || !tradeDate || point === null) {
    return null
  }
  return {
    tradeDate,
    value: point,
    percentile5y: asNumber(row.percentile_5y),
    bandMean: asNumber(row.band_mean),
    bandUpper: asNumber(row.band_upper),
    bandLower: asNumber(row.band_lower),
    qualityFlag: asString(row.quality_flag) ?? "ok",
    extras: parseExtras(row.extras),
  }
}

function parseIndicator(value: unknown): Indicator | null {
  const row = asRecord(value)
  const code = asString(row?.code)
  const name = asString(row?.name)
  if (!row || !code || !INDICATOR_CODES.includes(code) || !name) {
    return null
  }
  return {
    code: code as IndicatorCode,
    name,
    unit: asString(row.unit) ?? "",
    definition: asString(row.definition) ?? "",
    status: status(row.status),
    reason: asString(row.reason),
    zone: asString(row.zone),
    percentileNote: asString(row.percentile_note),
    latest: parseIndicatorPoint(row.latest),
    points: asList(row.points).flatMap((item) => {
      const point = parseIndicatorPoint(item)
      return point ? [point] : []
    }),
    inputs: listOf(row.inputs).flatMap((input) => {
      const inputName = asString(input.name)
      return inputName
        ? [
            {
              name: inputName,
              source: asString(input.source) ?? "",
              lastDate: asString(input.last_date),
              rows: asNumber(input.rows) ?? 0,
            },
          ]
        : []
    }),
    notes: strings(row.notes),
  }
}

export function parseValuation(body: unknown): ValuationView | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  return {
    indicators: asList(data.indicators).flatMap((item) => {
      const indicator = parseIndicator(item)
      return indicator ? [indicator] : []
    }),
    rulesVersion: asString(data.rules_version) ?? "",
  }
}

export function parseSources(body: unknown): SourcesView | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  return {
    sources: listOf(data.sources).flatMap((row) => {
      const key = asString(row.key)
      const label = asString(row.label)
      if (!key || !label) {
        return []
      }
      return [
        {
          key,
          label,
          provider: asString(row.provider) ?? "",
          url: asString(row.url) ?? "",
          frequency: asString(row.frequency) ?? "",
          status: status(row.status),
          rows: asNumber(row.rows) ?? 0,
          firstDate: asString(row.first_date),
          lastDate: asString(row.last_date),
          reviewRows: asNumber(row.review_rows) ?? 0,
          fetchedAt: asString(row.fetched_at),
          note: asString(row.note) ?? "",
        },
      ]
    }),
    recentIssues: listOf(data.recent_issues).flatMap((row) => {
      const tradeDate = asString(row.trade_date)
      const subject = asString(row.subject)
      return tradeDate && subject ? [{ tradeDate, subject, note: asString(row.note) ?? "" }] : []
    }),
    rulesVersion: asString(data.rules_version) ?? "",
  }
}

async function getBody(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(apiUrl(path), {
    headers: { Accept: "application/json" },
    credentials: "include",
    signal,
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return body
}

function required<T>(parsed: T | null, what: string): T {
  if (parsed === null) {
    throw new ApiError(`${what}返回的数据格式不对`, "BAD_PAYLOAD", null)
  }
  return parsed
}

export async function fetchPcr(group: PcrGroupCode, signal?: AbortSignal): Promise<PcrView> {
  return required(parsePcr(await getBody(`/api/v1/custom-analysis/pcr/${group}`, signal)), "期权 PCR")
}

export async function fetchValuation(signal?: AbortSignal): Promise<ValuationView> {
  return required(parseValuation(await getBody("/api/v1/custom-analysis/valuation", signal)), "估值指标")
}

export async function fetchSources(signal?: AbortSignal): Promise<SourcesView> {
  return required(parseSources(await getBody("/api/v1/custom-analysis/sources", signal)), "数据源清单")
}

export function pcrCsvUrl(group: PcrGroupCode, kind: "etf" | "index"): string {
  return apiUrl(`/api/v1/custom-analysis/export/pcr/${group}.csv?kind=${kind}`)
}

export function valuationCsvUrl(): string {
  return apiUrl("/api/v1/custom-analysis/export/valuation.csv")
}
