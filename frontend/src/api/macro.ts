export type Availability = "unavailable" | "configured" | "not_required" | "public_csv"

export interface MacroSeriesItem {
  series_code: string
  name_cn: string
  name_en: string
  frequency: string
  base_unit: string
  seasonal_adjustment: string
}

export interface MacroCatalog {
  availability: Availability
  notice: string
  series: MacroSeriesItem[]
}

export interface MacroObservation {
  observation_date: string
  value: string
  unit: string
  realtime_start: string
  revision_no: number
}

export function providerStatusLabel(input: {
  credentialRequired: boolean
  availability: string
}): string {
  if (input.availability === "unavailable") {
    return "不可用"
  }
  if (input.availability === "public_csv") {
    return "公开数据（无需密钥）"
  }
  if (!input.credentialRequired) {
    return "不需要密钥"
  }
  return "已提供密钥"
}

/** 宏观数据源状态的一句话说明，用于页头/页脚的数据来源条。 */
export function macroSourceLabel(availability: string): string {
  if (availability === "public_csv") {
    return "FRED 公开 CSV（最新修订值）"
  }
  if (availability === "configured") {
    return "FRED 官方接口（按发布日回放）"
  }
  if (availability === "not_required") {
    return "FRED"
  }
  if (availability === "unavailable") {
    return "FRED（数据源暂不可用）"
  }
  return "FRED"
}

const FREQUENCY_LABELS: Record<string, string> = {
  Daily: "日度",
  Weekly: "周度",
  Monthly: "月度",
  Quarterly: "季度",
  Annual: "年度",
}

export function frequencyLabel(frequency: string): string {
  return FREQUENCY_LABELS[frequency] ?? frequency
}

const ADJUSTMENT_LABELS: Record<string, string> = {
  "Seasonally Adjusted": "季调",
  "Not Seasonally Adjusted": "未季调",
  "Seasonally Adjusted Annual Rate": "季调年化",
}

export function adjustmentLabel(adjustment: string): string {
  return ADJUSTMENT_LABELS[adjustment] ?? adjustment
}

/** 原始单位 → 展示单位。指数类保留基期，便于和官方口径对照。 */
export function unitLabel(unit: string): string {
  if (unit === "Percent") {
    return "%"
  }
  if (unit === "Billions of Dollars") {
    return "十亿美元"
  }
  if (unit === "Thousands of Persons") {
    return "千人"
  }
  if (unit === "Index") {
    return "指数"
  }
  if (unit === "CNY") {
    return "元"
  }
  const index = /^Index (.+)=100$/.exec(unit)
  if (index) {
    return `指数（${index[1]}=100）`
  }
  return unit
}

/**
 * 同比：每个观测值找 12 个月前同月的观测值计算 (当期/上年同期 - 1) × 100。
 * 找不到上年同月或上年值为 0 的点直接跳过，不插值、不外推。
 */
export function yearOverYear(rows: readonly { date: string; value: number }[]): { date: string; value: number }[] {
  const byMonth = new Map<string, number>()
  for (const row of rows) {
    byMonth.set(row.date.slice(0, 7), row.value)
  }
  return rows.flatMap((row) => {
    const year = Number(row.date.slice(0, 4))
    const month = row.date.slice(5, 7)
    if (!Number.isFinite(year) || month.length !== 2) {
      return []
    }
    const base = byMonth.get(`${year - 1}-${month}`)
    if (base === undefined || base === 0) {
      return []
    }
    return [{ date: row.date, value: (row.value / base - 1) * 100 }]
  })
}

/** 规则依据行："<序列代码> <观测日> <数值> 公布日 <公布日>"；格式不符时只回原文。 */
export function parseEvidence(line: string): {
  code: string
  date: string
  value: number | null
  published: string
  raw: string
} {
  const match = /^(\S+)\s+(\d{4}-\d{2}-\d{2})\s+(\S+)(?:\s+公布日\s+(\S+))?/.exec(line.trim())
  if (!match) {
    return { code: "", date: "", value: null, published: "", raw: line }
  }
  const value = Number(match[3])
  return {
    code: match[1] ?? "",
    date: match[2] ?? "",
    value: Number.isFinite(value) ? value : null,
    published: match[4] ?? "",
    raw: line,
  }
}

const REGIME_WORDS: Record<string, string> = { up: "上行", down: "下行", easy: "偏松", tight: "偏紧" }

/** 组合状态 "<增长>-<通胀>-<流动性>" → 中文；数据不足时返回统一说明。 */
export function regimeText(code: string): string {
  if (!code) {
    return ""
  }
  if (code === "insufficient_data") {
    return "数据不足，暂不判断"
  }
  const [growth, inflation, liquidity] = code.split("-")
  const parts: string[] = []
  if (growth) {
    parts.push(`增长${REGIME_WORDS[growth] ?? growth}`)
  }
  if (inflation) {
    parts.push(`通胀${REGIME_WORDS[inflation] ?? inflation}`)
  }
  if (liquidity) {
    parts.push(`流动性${REGIME_WORDS[liquidity] ?? liquidity}`)
  }
  return parts.join(" · ")
}

/**
 * ECharts 用 canvas 绘制，读不懂 `var(--x)`；这里在绘制前把设计变量解析成实际颜色。
 */
export function cssColor(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback
  }
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function chartPolyline(values: number[], width = 280, height = 100): string | null {
  if (values.length < 2) {
    return null
  }
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = high - low || 1
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width
      const y = height - ((value - low) / span) * (height - 8) - 4
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(" ")
}

export function scoreText(input: { value: number | null; evidence: string[] }): string {
  if (input.value === null) {
    return "数据不足，没有编造分数"
  }
  const evidence = input.evidence.length ? `。证据：${input.evidence.join("；")}` : ""
  return `${input.value}${evidence}`
}

export function describeObservations(input: {
  emptyMessage: string | null
  observations: MacroObservation[]
}): string[] {
  if (input.observations.length === 0) {
    return [input.emptyMessage ?? "该日期还看不到已公布的数值"]
  }
  return input.observations.map(
    (item) =>
      `${item.observation_date} · ${item.value} ${item.unit} · 发布日次日可见 ${item.realtime_start} · 第 ${item.revision_no} 次`,
  )
}
