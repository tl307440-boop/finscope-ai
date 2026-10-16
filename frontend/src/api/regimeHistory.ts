import { apiUrl, errorFrom, readJson } from "./client"
import { asNumber, asRecord, asString, listOf } from "./json"

export interface RegimeSnapshotRow {
  asOf: string
  regimeCode: string
  growth: number | null
  inflation: number | null
  liquidity: number | null
  ruleVersion: string
  computedAt: string
}

export interface RegimeHistory {
  items: RegimeSnapshotRow[]
  notice: string
}

export function parseRegimeHistory(body: unknown): RegimeHistory {
  const data = asRecord(asRecord(body)?.data)
  const items: RegimeSnapshotRow[] = []
  for (const record of listOf(data?.items)) {
    const asOf = asString(record.as_of)
    const regimeCode = asString(record.regime_code)
    if (!asOf || !regimeCode) {
      continue
    }
    items.push({
      asOf,
      regimeCode,
      growth: asNumber(record.growth),
      inflation: asNumber(record.inflation),
      liquidity: asNumber(record.liquidity),
      ruleVersion: asString(record.rule_version) ?? "",
      computedAt: asString(record.computed_at) ?? "",
    })
  }
  return { items, notice: asString(data?.notice) ?? "" }
}

export async function fetchRegimeHistory(
  limit = 30,
  fetcher: typeof fetch = fetch,
): Promise<RegimeHistory> {
  const response = await fetcher(apiUrl(`/api/v1/macro/regime-history?limit=${limit}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseRegimeHistory(body)
}
