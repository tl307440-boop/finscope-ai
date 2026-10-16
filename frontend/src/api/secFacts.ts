import { asNumber, asRecord, asString, scaleAmount } from "./json"

export interface SecFactLine {
  concept: string
  unit: string
  value: string
  filed: string
  accessionNo: string
  sourceUrl: string
  form: string
}

export function describeFacts(input: { notice: string | null; facts: SecFactLine[] }): string[] {
  const notice = input.notice ?? "本地样例，不是本次从 SEC 拉取。"
  if (input.facts.length === 0) {
    return [notice, "该日期还看不到已申报的事实"]
  }
  return [
    notice,
    ...input.facts.map(
      (fact) =>
        `${fact.concept} ${fact.value} ${fact.unit} · ${fact.form} · 申报 ${fact.filed} · ${fact.accessionNo} · ${fact.sourceUrl}`,
    ),
  ]
}

/** /sec/facts 的一条已申报事实，数值已转成 number，原文保留在 raw。 */
export interface FactPoint {
  concept: string
  unit: string
  value: number
  raw: string
  periodStart: string
  periodEnd: string
  form: string
  filed: string
  fiscalYear: number | null
  fiscalPeriod: string
  accessionNo: string
  sourceUrl: string
}

/** instant = 时点（资产负债表），quarter = 单季，ytd = 年初至今累计，annual = 全年。 */
export type PeriodKind = "instant" | "quarter" | "ytd" | "annual"

export function parseFactPoint(input: unknown): FactPoint[] {
  const record = asRecord(input)
  const concept = asString(record?.concept)
  const raw = asString(record?.value)
  const periodEnd = asString(record?.period_end)
  const value = asNumber(record?.value)
  if (!concept || !raw || !periodEnd || value === null) {
    return []
  }
  return [
    {
      concept,
      unit: asString(record?.unit) ?? "",
      value,
      raw,
      periodStart: asString(record?.period_start) ?? "",
      periodEnd,
      form: asString(record?.form) ?? "",
      filed: asString(record?.filed) ?? "",
      fiscalYear: asNumber(record?.fiscal_year),
      fiscalPeriod: asString(record?.fiscal_period) ?? "",
      accessionNo: asString(record?.accession_no) ?? "",
      sourceUrl: asString(record?.source_url) ?? "",
    },
  ]
}

/** 常见 us-gaap 概念的中文名；键是去掉命名空间前缀后的短名。 */
const CONCEPT_LABELS: Record<string, string> = {
  Revenues: "营业收入",
  RevenueFromContractWithCustomerExcludingAssessedTax: "营业收入",
  RevenueFromContractWithCustomerIncludingAssessedTax: "营业收入",
  SalesRevenueNet: "营业收入",
  CostOfGoodsAndServicesSold: "营业成本",
  CostOfRevenue: "营业成本",
  GrossProfit: "毛利",
  OperatingExpenses: "营业费用",
  ResearchAndDevelopmentExpense: "研发费用",
  SellingGeneralAndAdministrativeExpense: "销售及管理费用",
  OperatingIncomeLoss: "营业利润",
  IncomeTaxExpenseBenefit: "所得税费用",
  NetIncomeLoss: "净利润",
  EarningsPerShareBasic: "基本 EPS",
  EarningsPerShareDiluted: "稀释 EPS",
  NetCashProvidedByUsedInOperatingActivities: "经营现金流",
  NetCashProvidedByUsedInInvestingActivities: "投资现金流",
  NetCashProvidedByUsedInFinancingActivities: "筹资现金流",
  PaymentsToAcquirePropertyPlantAndEquipment: "资本开支",
  Assets: "总资产",
  AssetsCurrent: "流动资产",
  Liabilities: "总负债",
  LiabilitiesCurrent: "流动负债",
  StockholdersEquity: "股东权益",
  CashAndCashEquivalentsAtCarryingValue: "现金及等价物",
  AccountsReceivableNetCurrent: "应收账款",
  InventoryNet: "存货",
  LongTermDebt: "长期债务",
  LongTermDebtNoncurrent: "长期债务",
  CommonStockSharesOutstanding: "流通股数",
}

export function conceptShortName(concept: string): string {
  return concept.replace(/^[A-Za-z-]+:/, "")
}

/** 映射表里没有的概念显示英文短名（去掉 us-gaap: 前缀），不猜中文名。 */
export function conceptLabel(concept: string): string {
  const short = conceptShortName(concept)
  return CONCEPT_LABELS[short] ?? short
}

const UNIT_LABELS: Record<string, string> = {
  USD: "美元",
  CNY: "元",
  RMB: "元",
  HKD: "港元",
  EUR: "欧元",
  SHARES: "股",
  PURE: "倍",
}

/** XBRL 单位 → 中文；「USD/shares」→「美元/股」。 */
export function unitLabel(unit: string): string {
  return unit
    .split("/")
    .map((part) => UNIT_LABELS[part.trim().toUpperCase()] ?? part)
    .join("/")
}

export function isPerShare(unit: string): boolean {
  return /\/shares$/i.test(unit)
}

/** 金额按「亿/万」缩写，每股数两位小数；返回值和单位分开，方便大数字配小单位排版。 */
export function factDisplay(value: number | null, unit: string): { text: string; unit: string } {
  if (value === null || !Number.isFinite(value)) {
    return { text: "—", unit: "" }
  }
  const label = unitLabel(unit)
  if (isPerShare(unit) || unit.toUpperCase() === "PURE") {
    return { text: value.toFixed(2), unit: label }
  }
  const scaled = scaleAmount(value)
  return { text: scaled.text, unit: `${scaled.scale}${label}` }
}

export function formatFact(value: number | null, unit: string): string {
  const shown = factDisplay(value, unit)
  return shown.text === "—" ? "—" : `${shown.text} ${shown.unit}`.trim()
}

const DAY_MS = 86_400_000

function dayNumber(text: string): number | null {
  const time = new Date(`${text}T00:00:00Z`).getTime()
  return Number.isFinite(time) ? Math.round(time / DAY_MS) : null
}

/** later - earlier 的天数；任一日期无法解析时返回 null。 */
export function daysBetween(earlier: string, later: string): number | null {
  const start = dayNumber(earlier)
  const end = dayNumber(later)
  return start === null || end === null ? null : end - start
}

export function shiftDays(text: string, days: number): string {
  const base = dayNumber(text)
  if (base === null) {
    return text
  }
  return new Date((base + days) * DAY_MS).toISOString().slice(0, 10)
}

export function periodSpan(point: Pick<FactPoint, "periodStart" | "periodEnd">): number | null {
  return point.periodStart ? daysBetween(point.periodStart, point.periodEnd) : null
}

export function periodKind(point: Pick<FactPoint, "periodStart" | "periodEnd">): PeriodKind {
  const span = periodSpan(point)
  if (span === null) {
    return "instant"
  }
  if (span <= 100) {
    return "quarter"
  }
  return span >= 340 ? "annual" : "ytd"
}

export function periodKindLabel(point: Pick<FactPoint, "periodStart" | "periodEnd">): string {
  const kind = periodKind(point)
  if (kind === "instant") {
    return "期末时点"
  }
  if (kind === "quarter") {
    return "单季"
  }
  if (kind === "annual") {
    return "全年"
  }
  const quarters = Math.round((periodSpan(point) ?? 0) / 91)
  return `前 ${quarters} 季累计`
}

/** 同一概念、同一单位、同一期间长度（±20 天）才算可比口径。 */
export function sameShape(left: FactPoint, right: FactPoint): boolean {
  if (left.concept !== right.concept || left.unit !== right.unit) {
    return false
  }
  const leftSpan = periodSpan(left)
  const rightSpan = periodSpan(right)
  if (leftSpan === null || rightSpan === null) {
    return leftSpan === rightSpan
  }
  return Math.abs(leftSpan - rightSpan) <= 20
}

/** 两个期末是否相隔约一年，用来挑默认的对比报告期。 */
export function isYearApart(earlier: string, later: string): boolean {
  const gap = daysBetween(earlier, later)
  return gap !== null && Math.abs(gap - 365) <= 14
}

/** 页面上的一个指标：按优先级排列的候选概念（不同公司用的收入概念不一样）。 */
export interface MetricDef {
  key: string
  label: string
  concepts: readonly string[]
}

export const METRICS: Record<string, MetricDef> = {
  revenue: {
    key: "revenue",
    label: "营业收入",
    concepts: [
      "RevenueFromContractWithCustomerExcludingAssessedTax",
      "Revenues",
      "SalesRevenueNet",
      "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
  },
  cost: { key: "cost", label: "营业成本", concepts: ["CostOfGoodsAndServicesSold", "CostOfRevenue"] },
  grossProfit: { key: "grossProfit", label: "毛利", concepts: ["GrossProfit"] },
  operatingIncome: { key: "operatingIncome", label: "营业利润", concepts: ["OperatingIncomeLoss"] },
  netIncome: { key: "netIncome", label: "净利润", concepts: ["NetIncomeLoss"] },
  rnd: { key: "rnd", label: "研发费用", concepts: ["ResearchAndDevelopmentExpense"] },
  epsDiluted: { key: "epsDiluted", label: "稀释 EPS", concepts: ["EarningsPerShareDiluted"] },
  operatingCashFlow: {
    key: "operatingCashFlow",
    label: "经营现金流",
    concepts: ["NetCashProvidedByUsedInOperatingActivities"],
  },
  assets: { key: "assets", label: "总资产", concepts: ["Assets"] },
  currentAssets: { key: "currentAssets", label: "流动资产", concepts: ["AssetsCurrent"] },
  liabilities: { key: "liabilities", label: "总负债", concepts: ["Liabilities"] },
  currentLiabilities: { key: "currentLiabilities", label: "流动负债", concepts: ["LiabilitiesCurrent"] },
  equity: { key: "equity", label: "股东权益", concepts: ["StockholdersEquity"] },
  cash: { key: "cash", label: "现金及等价物", concepts: ["CashAndCashEquivalentsAtCarryingValue"] },
  receivables: { key: "receivables", label: "应收账款", concepts: ["AccountsReceivableNetCurrent"] },
  inventory: { key: "inventory", label: "存货", concepts: ["InventoryNet"] },
}

/** 报告期选择器的默认值：最新一期 + 上一年同期（没有就取上一期）。 */
export function defaultPeriods(available: readonly string[]): { earlier: string; later: string } {
  const sorted = [...available].sort()
  const later = sorted[sorted.length - 1] ?? ""
  if (!later) {
    return { earlier: "", later: "" }
  }
  const yearAgo = sorted.find((period) => period !== later && isYearApart(period, later))
  return { earlier: yearAgo ?? sorted[sorted.length - 2] ?? "", later }
}

/** 本地日期 yyyy-MM-dd（「今天」作为默认观察日）。 */
export function todayText(now: Date = new Date()): string {
  const pad = (part: number): string => String(part).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

const FORM_LABELS: Record<string, string> = {
  "10-K": "年度报告",
  "10-K/A": "年度报告（修订）",
  "10-Q": "季度报告",
  "10-Q/A": "季度报告（修订）",
  "8-K": "重大事项报告",
  "20-F": "年度报告（外国发行人）",
  "6-K": "外国发行人临时报告",
}

export function formLabel(form: string): string {
  const label = FORM_LABELS[form.toUpperCase()]
  return label ? `${form} ${label}` : form
}

const FILING_STATUS_LABELS: Record<string, string> = {
  edgar: "SEC 官方",
  eastmoney: "东方财富",
  sample: "样例",
}

export function filingStatusLabel(status: string): string {
  return FILING_STATUS_LABELS[status.toLowerCase()] ?? status
}
