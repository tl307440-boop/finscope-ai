import { ApiError, apiUrl, errorFrom, readJson } from "./client"
import { asBool, asString, dataOf, listOf } from "./json"

/** 证券搜索结果行，对应 backend/app/schemas/instruments.py:12 InstrumentHit。 */
export interface InstrumentHit {
  id: string
  canonicalCode: string
  displaySymbol: string
  exchange: string
  symbol: string
  nameCn: string
  nameEn: string
  instrumentType: "STOCK" | "INDEX" | "ETF"
  market: "CN" | "US"
  cik: string | null
  currency: string
}

export interface InstrumentSearchResult {
  query: string
  unique: boolean
  emptyMessage: string | null
  results: InstrumentHit[]
}

function parseHit(value: unknown): InstrumentHit | null {
  const record = value as Record<string, unknown> | null
  if (!record || typeof record !== "object") {
    return null
  }
  const id = asString(record.id)
  const canonicalCode = asString(record.canonical_code)
  const displaySymbol = asString(record.display_symbol) ?? canonicalCode ?? ""
  const market = asString(record.market)
  if (!id || !canonicalCode || (market !== "CN" && market !== "US")) {
    return null
  }
  const instrumentType = asString(record.instrument_type)
  return {
    id,
    canonicalCode,
    displaySymbol,
    exchange: asString(record.exchange) ?? "",
    symbol: asString(record.symbol) ?? "",
    nameCn: asString(record.name_cn) ?? "",
    nameEn: asString(record.name_en) ?? "",
    instrumentType:
      instrumentType === "STOCK" || instrumentType === "INDEX" || instrumentType === "ETF"
        ? instrumentType
        : "STOCK",
    market,
    cik: asString(record.cik),
    currency: asString(record.currency) ?? (market === "CN" ? "CNY" : "USD"),
  }
}

/** 解析搜索响应；返回结果集用于下拉建议。 */
export function parseSearch(body: unknown): InstrumentSearchResult {
  const data = dataOf(body)
  return {
    query: asString(data?.query) ?? "",
    unique: asBool(data?.unique) ?? false,
    emptyMessage: asString(data?.empty_message),
    results: listOf(data?.results).flatMap((row) => {
      const hit = parseHit(row)
      return hit ? [hit] : []
    }),
  }
}

let controller: AbortController | null = null

/**
 * GET /api/v1/instruments/search
 * 规格书 §1.2 要求全局搜索支持 A股代码 / 名称 / 美股 ticker / 别名，
 * 无匹配时展示“暂无该证券”。后端返回 empty_message，前端不自行编造。
 */
export async function searchInstruments(
  query: string,
  market?: "CN" | "US",
  limit = 10,
): Promise<InstrumentSearchResult> {
  controller?.abort()
  controller = new AbortController()
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  if (market) {
    params.set("market", market)
  }
  const response = await fetch(apiUrl(`/api/v1/instruments/search?${params.toString()}`), {
    signal: controller.signal,
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseSearch(body)
}

/** 把搜索结果映射成路由目标（规格书 §1.3 的 /company/:symbol 与 /market/stocks/:symbol）。 */
export function routeForHit(hit: InstrumentHit): string {
  const code = encodeURIComponent(hit.canonicalCode)
  return hit.market === "CN" ? `/market/stocks/${code}` : `/company/${code}`
}

export function describeHit(hit: InstrumentHit): string {
  const name = hit.nameCn || hit.nameEn || hit.canonicalCode
  return `${hit.exchange} · ${hit.canonicalCode} · ${name}`
}

export { ApiError }
