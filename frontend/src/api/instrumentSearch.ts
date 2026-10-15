export interface SearchHit {
  canonical_code: string
  name_cn: string
  instrument_type: string
  display_symbol: string
}

export interface SearchPayload {
  unique: boolean
  empty_message: string | null
  results: SearchHit[]
}

export function summarizeSearch(payload: SearchPayload): string[] {
  if (payload.results.length === 0) {
    return [payload.empty_message ?? "暂无该证券"]
  }
  if (payload.unique && payload.results.length === 1) {
    const hit = payload.results[0]
    const kind = hit.instrument_type === "INDEX" ? "指数" : "证券"
    return [`唯一交易所代码 ${hit.canonical_code}`, `${hit.name_cn} · ${kind} · ${hit.display_symbol}`]
  }
  return [
    "匹配到多条证券，请用带交易所后缀的代码",
    ...payload.results.map((hit) => `${hit.canonical_code} ${hit.name_cn}`),
  ]
}
