import { apiUrl, errorFrom, readJson } from "./client"
import { asRecord, asString, listOf } from "./json"

export interface MetricSnapshotItem {
  code: string
  label: string
  value: string | null
  unit: string
  periodEnd: string
  periodType: string
  reason: string | null
  sources: string[]
}

export interface MetricSnapshot {
  status: "ready" | "empty"
  notice: string
  items: MetricSnapshotItem[]
}

export const METRIC_LABELS: Record<string, string> = {
  revenue_yoy: "营收同比",
  net_income_yoy: "净利润同比",
  gross_margin: "毛利率",
  debt_ratio: "资产负债率",
  current_ratio: "流动比率",
  cfo_to_net_income: "经营现金流 / 净利润",
}

const ORDER = Object.keys(METRIC_LABELS)

export function parseMetricSnapshot(body: unknown): MetricSnapshot {
  const data = asRecord(asRecord(body)?.data)
  const items: MetricSnapshotItem[] = []
  for (const record of listOf(data?.items)) {
    const code = asString(record.metric_code)
    if (!code) {
      continue
    }
    const sources = listOf(record.inputs)
      .map((input) => asString(input.source_url))
      .filter((url): url is string => url !== null)
    items.push({
      code,
      label: METRIC_LABELS[code] ?? code,
      value: asString(record.value),
      unit: asString(record.unit) ?? "",
      periodEnd: asString(record.period_end) ?? "",
      periodType: asString(record.period_type) ?? "",
      reason: asString(record.reason),
      sources: [...new Set(sources)],
    })
  }
  items.sort((left, right) => {
    const a = ORDER.indexOf(left.code)
    const b = ORDER.indexOf(right.code)
    return (a === -1 ? ORDER.length : a) - (b === -1 ? ORDER.length : b)
  })
  return {
    status: items.length > 0 ? "ready" : "empty",
    notice: asString(data?.notice) ?? "",
    items,
  }
}

export function formatMetric(item: MetricSnapshotItem): string {
  if (item.value === null) {
    return "—"
  }
  const number = Number(item.value)
  const shown = Number.isFinite(number) ? number.toFixed(2) : item.value
  return item.unit === "%" ? `${shown}%` : `${shown}${item.unit}`
}

export async function fetchMetricSnapshot(
  instrumentId: string,
  fetcher: typeof fetch = fetch,
): Promise<MetricSnapshot> {
  const response = await fetcher(apiUrl(`/api/v1/companies/${instrumentId}/metrics`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseMetricSnapshot(body)
}
