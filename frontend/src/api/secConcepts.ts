import { scaleAmount } from "./json"

/** SEC XBRL 科目 → 中文名。没收录的科目统一叫「其他财务科目」，界面上不露原始概念名。 */
const CONCEPT_LABELS: Record<string, string> = {
  "us-gaap:Revenues": "营业收入",
  "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": "营业收入",
  "us-gaap:CostOfGoodsAndServicesSold": "营业成本",
  "us-gaap:CostOfRevenue": "营业成本",
  "us-gaap:GrossProfit": "毛利",
  "us-gaap:OperatingIncomeLoss": "营业利润",
  "us-gaap:NetIncomeLoss": "净利润",
  "us-gaap:EarningsPerShareDiluted": "稀释每股收益",
  "us-gaap:EarningsPerShareBasic": "基本每股收益",
  "us-gaap:ResearchAndDevelopmentExpense": "研发费用",
  "us-gaap:NetCashProvidedByUsedInOperatingActivities": "经营活动现金流",
  "us-gaap:Assets": "总资产",
  "us-gaap:AssetsCurrent": "流动资产",
  "us-gaap:Liabilities": "总负债",
  "us-gaap:LiabilitiesCurrent": "流动负债",
  "us-gaap:StockholdersEquity": "股东权益",
  "us-gaap:CashAndCashEquivalentsAtCarryingValue": "现金及现金等价物",
  "us-gaap:InventoryNet": "存货",
  "us-gaap:AccountsReceivableNetCurrent": "应收账款",
}

export function conceptLabel(concept: string): string {
  return CONCEPT_LABELS[concept] ?? "其他财务科目"
}

const UNIT_NAMES: Record<string, string> = {
  USD: "美元",
  CNY: "元",
  shares: "股",
  "USD/shares": "美元/股",
  "CNY/shares": "元/股",
  TWD: "新台币",
  JPY: "日元",
  EUR: "欧元",
  GBP: "英镑",
  CHF: "瑞士法郎",
  CAD: "加元",
  DKK: "丹麦克朗",
  "TWD/shares": "新台币/股",
  "EUR/shares": "欧元/股",
  "JPY/shares": "日元/股",
  "GBP/shares": "英镑/股",
}

export function unitLabel(unit: string): string {
  return UNIT_NAMES[unit] ?? unit
}

/** 每股类单位不做量级缩写；金额与股数按「亿/万」缩写，避免出现 21837000000 这种长串。 */
export function conceptAmount(value: number | null, unit: string): { text: string; unit: string } {
  if (value === null || !Number.isFinite(value)) {
    return { text: "—", unit: "" }
  }
  const name = unitLabel(unit)
  if (unit.includes("/")) {
    return { text: value.toFixed(2), unit: name }
  }
  const scaled = scaleAmount(value, 2)
  return { text: scaled.text, unit: `${scaled.scale}${name}` }
}

export function formatConcept(value: number | null, unit: string): string {
  const amount = conceptAmount(value, unit)
  return amount.text === "—" ? "—" : `${amount.text} ${amount.unit}`.trim()
}
