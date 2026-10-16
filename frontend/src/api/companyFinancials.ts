/**
 * 公司财务总览（GET /api/v1/companies/{id}/financials）。
 * 同比、环比、毛利率、资产负债率与规则读数都由后端按申报口径配对后算好，这里只解析与排版。
 */
import { apiUrl, errorFrom, readJson } from "./client"
import { asRecord, asString, dataOf, listOf } from "./json"

export interface FinanceSource {
  form: string
  filed: string
  accessionNo: string
  sourceUrl: string
  periodEnd: string
  periodLabel: string
  unit: string
}

export interface FinanceTile {
  key: string
  label: string
  kind: "amount" | "ratio"
  value: number | null
  unit: string
  delta: number | null
  deltaUnit: "%" | "pct"
  spark: number[]
  sparkKind: "bar" | "line"
  formula: string
  source: FinanceSource | null
}

export interface TrendPoint {
  periodEnd: string
  value: number
  yoy: number | null
}

export interface TrendSeries {
  key: string
  label: string
  unit: string
  points: TrendPoint[]
}

export interface TableRow {
  key: string
  label: string
  kind: "amount" | "ratio"
  unit: string
  cells: (number | null)[]
  yoy: number | null
  qoq: number | null
  deltaUnit: "%" | "pct"
}

export interface TableBlock {
  columns: string[]
  rows: TableRow[]
}

export interface CashProfitRow {
  periodEnd: string
  unit: string
  operatingCashFlow: number | null
  netIncome: number | null
  /** 经营现金流 ÷ 净利润；净利润不为正时后端不给倍数 */
  multiple: number | null
}

export type FinanceMode = "quarter" | "annual"

export interface CompanyFinancials {
  dataStatus: "edgar" | "sample" | "empty"
  notice: string
  referenceEnd: string
  compareEnd: string
  deltaLabel: string
  tiles: FinanceTile[]
  trend: Record<FinanceMode, TrendSeries[]>
  table: Record<FinanceMode, TableBlock>
  cashVsProfit: CashProfitRow[]
  ruleObservations: Record<string, string>
}

const num = (value: unknown): number | null => {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value !== "string" || value.trim() === "") {
    return null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

const deltaUnit = (value: unknown): "%" | "pct" => (value === "pct" ? "pct" : "%")

function parseSource(value: unknown): FinanceSource | null {
  const record = asRecord(value)
  if (!record) {
    return null
  }
  return {
    form: asString(record.form) ?? "",
    filed: asString(record.filed) ?? "",
    accessionNo: asString(record.accession_no) ?? "",
    sourceUrl: asString(record.source_url) ?? "",
    periodEnd: asString(record.period_end) ?? "",
    periodLabel: asString(record.period_label) ?? "",
    unit: asString(record.unit) ?? "",
  }
}

function parseTable(value: unknown): TableBlock {
  const record = asRecord(value)
  const columns = Array.isArray(record?.columns)
    ? record.columns.filter((item): item is string => typeof item === "string")
    : []
  return {
    columns,
    rows: listOf(record?.rows).map((row) => {
      const cells = Array.isArray(row.cells) ? row.cells.map(num) : []
      return {
        key: asString(row.key) ?? "",
        label: asString(row.label) ?? "",
        kind: row.kind === "ratio" ? "ratio" : "amount",
        unit: asString(row.unit) ?? "",
        cells: columns.map((_, index) => cells[index] ?? null),
        yoy: num(row.yoy),
        qoq: num(row.qoq),
        deltaUnit: deltaUnit(row.delta_unit),
      }
    }),
  }
}

function parseTrend(value: unknown): TrendSeries[] {
  return listOf(value).map((item) => ({
    key: asString(item.key) ?? "",
    label: asString(item.label) ?? "",
    unit: asString(item.unit) ?? "",
    points: listOf(item.points).flatMap((point) => {
      const amount = num(point.value)
      const periodEnd = asString(point.period_end)
      return amount === null || !periodEnd ? [] : [{ periodEnd, value: amount, yoy: num(point.yoy) }]
    }),
  }))
}

export function parseFinancials(body: unknown): CompanyFinancials | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  const trend = asRecord(data.trend)
  const table = asRecord(data.table)
  const observations = asRecord(data.rule_observations) ?? {}
  const status = asString(data.data_status)
  return {
    dataStatus: status === "edgar" || status === "sample" ? status : "empty",
    notice: asString(data.notice) ?? "",
    referenceEnd: asString(data.reference_end) ?? "",
    compareEnd: asString(data.compare_end) ?? "",
    deltaLabel: asString(data.delta_label) ?? "同比",
    tiles: listOf(data.tiles).map((tile) => ({
      key: asString(tile.key) ?? "",
      label: asString(tile.label) ?? "",
      kind: tile.kind === "ratio" ? "ratio" : "amount",
      value: num(tile.value),
      unit: asString(tile.unit) ?? "",
      delta: num(tile.delta),
      deltaUnit: deltaUnit(tile.delta_unit),
      spark: Array.isArray(tile.spark) ? tile.spark.flatMap((item) => num(item) ?? []) : [],
      sparkKind: tile.spark_kind === "line" ? "line" : "bar",
      formula: asString(tile.formula) ?? "",
      source: parseSource(tile.source),
    })),
    trend: { quarter: parseTrend(trend?.quarter), annual: parseTrend(trend?.annual) },
    table: { quarter: parseTable(table?.quarter), annual: parseTable(table?.annual) },
    cashVsProfit: listOf(data.cash_vs_profit).flatMap((row) => {
      const periodEnd = asString(row.period_end)
      return periodEnd
        ? [
            {
              periodEnd,
              unit: asString(row.unit) ?? "",
              operatingCashFlow: num(row.operating_cash_flow),
              netIncome: num(row.net_income),
              multiple: num(row.multiple),
            },
          ]
        : []
    }),
    ruleObservations: Object.fromEntries(
      Object.entries(observations).flatMap(([key, text]) => (typeof text === "string" ? [[key, text]] : [])),
    ),
  }
}

export async function fetchFinancials(
  instrumentId: string,
  params: { asOf: string; reference?: string; compare?: string },
): Promise<CompanyFinancials | null> {
  const query = new URLSearchParams({ as_of: params.asOf })
  if (params.reference) {
    query.set("reference", params.reference)
  }
  if (params.compare && params.compare !== params.reference) {
    query.set("compare", params.compare)
  }
  const response = await fetch(apiUrl(`/api/v1/companies/${instrumentId}/financials?${query.toString()}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return parseFinancials(body)
}
