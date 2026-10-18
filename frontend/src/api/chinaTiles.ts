/**
 * 中国宏观指标带：18 格定义（顺序即页面顺序）。口径换算全部复用 macroTiles.buildTile。
 *
 * 不编造数据（AGENTS.md）：序列没有观测值时，该格显示「—」与「暂无数据」，
 * ? 提示里写明原因——后台尚未同步，或该指标没有已核实的公开自动来源、只能管理员导入 CSV。
 * 中国序列落库时已经是页面要显示的口径（同比 %、指数点、亿元），这里不再做同比换算。
 */
import { buildTile, type Observation, type TileCatalogItem, type TileSpec, type TileView } from "./macroTiles"

/** /api/v1/macro/china 每条序列里指标带用到的字段。 */
export interface ChinaSeriesInfo {
  code: string
  name: string
  unit: string
  frequency: string
  /** 最近一条观测写入时记录的来源名 */
  source: string
  sourceAgency: string
  manualOnly: boolean
}

const CUMULATIVE_NOTE = "累计同比 = 年初至当月累计值与上年同期累计值相比；1—2 月合并公布，没有 1 月单独数值"
const INDEX_NOTE = "景气指数，50 为荣枯线"

function pct(key: string, code: string, label: string, caption: string, note?: string): TileSpec {
  return {
    key,
    codes: [code],
    label,
    caption,
    transform: "level",
    unit: "%",
    deltaSuffix: " pct",
    digits: 1,
    ...(note ? { note } : {}),
  }
}

function index(key: string, code: string, label: string): TileSpec {
  return {
    key,
    codes: [code],
    label,
    caption: "指数",
    transform: "level",
    unit: "",
    deltaSuffix: " 点",
    digits: 1,
    note: INDEX_NOTE,
  }
}

function amount(key: string, code: string, label: string, caption: string): TileSpec {
  return {
    key,
    codes: [code],
    label,
    caption,
    transform: "level",
    unit: "亿元",
    deltaSuffix: " 亿元",
    digits: 0,
  }
}

export const CHINA_TILE_SPECS: readonly TileSpec[] = [
  pct("social-stock", "SOCIAL_FINANCING_STOCK_CN", "社融存量同比增速", "同比 (%)"),
  index("pmi-new-orders", "PMI_NEW_ORDERS_CN", "制造业PMI新订单"),
  pct("ppi", "PPI_CN", "PPI同比增速", "同比 (%)"),
  pct("profit", "INDUS_PROFIT_CN", "工业企业利润增速", "累计同比 (%)", `规模以上工业企业利润总额；${CUMULATIVE_NOTE}`),
  pct("m1", "M1_CN", "M1同比增速", "同比 (%)"),
  index("pmi", "PMI_CN", "制造业PMI"),
  pct("retail", "RETAIL_CN", "社会消费品零售总额", "当月同比 (%)"),
  pct("house-sales", "HOUSE_SALES_CN", "商品房销售金额", "累计同比 (%)", `新建商品房销售额；${CUMULATIVE_NOTE}`),
  pct(
    "core-cpi",
    "CORE_CPI_CN",
    "核心CPI同比增速",
    "同比 (%)",
    "不包括食品和能源的居民消费价格同比；同比 = 国家统计局公布指数（上年同月 = 100）− 100",
  ),
  pct("private-invest", "PRIVATE_INVEST_CN", "民间固定资产投资", "累计同比 (%)", CUMULATIVE_NOTE),
  pct("indus-value", "INDUS_CN", "规上工业增加值", "当月同比 (%)"),
  amount("ent-mlt-loan", "ENT_MLT_LOAN_CN", "企业中长期贷款", "当月增量 (亿元)"),
  pct(
    "gdp",
    "GDP_CN",
    "实际GDP",
    "当季同比 (%)",
    "当季同比（不是累计同比）；同比 = 国家统计局公布指数（上年同期 = 100）− 100；观测日取季首日",
  ),
  pct(
    "export",
    "EXPORT_CN",
    "出口金额",
    "当月同比 (%)",
    "海关总署口径，美元计价，经东方财富数据中心转载",
  ),
  amount("gov-bond", "GOV_BOND_NET_CN", "政府债券净融资", "社融口径 (亿元)"),
  pct(
    "fiscal",
    "FISCAL_SPEND_CN",
    "一般公共预算支出",
    "累计同比 (%)",
    `国家统计局口径「国家财政支出（不含债务还本）」；${CUMULATIVE_NOTE}`,
  ),
  index("pmi-export-orders", "PMI_NEW_EXPORT_ORDERS_CN", "制造业PMI新出口订单"),
  pct("mfg-invest", "MFG_INVEST_CN", "制造业固定资产投资", "累计同比 (%)", CUMULATIVE_NOTE),
]

export function chinaTileCodes(): string[] {
  return [...new Set(CHINA_TILE_SPECS.flatMap((spec) => spec.codes))]
}

const MANUAL_NOTE =
  "暂无数据：该指标来自中国人民银行调查统计司公布的统计表，不做自动抓取（对方 robots.txt 不允许），由管理员按月导入 CSV；导入前不显示任何数值"
const PENDING_NOTE = "暂无数据：后台每天从公开接口自动同步，首次同步完成前为空"

function toCatalog(series: readonly ChinaSeriesInfo[]): TileCatalogItem[] {
  return series.map((item) => ({
    code: item.code,
    name: item.name,
    nameEn: "",
    frequency: item.frequency,
    unit: item.unit,
    adjustment: "",
    provider: "china",
    sourceKey: "",
    sourceNote: `来源 ${item.source || item.sourceAgency || "未标注"}`,
  }))
}

export function buildChinaTiles(
  rowsByCode: Readonly<Record<string, readonly Observation[]>>,
  series: readonly ChinaSeriesInfo[],
): TileView[] {
  const catalog = toCatalog(series)
  return CHINA_TILE_SPECS.map((spec) => {
    const tile = buildTile(spec, rowsByCode, catalog)
    if (tile.value !== "—") {
      return tile
    }
    const info = series.find((item) => item.code === spec.codes[0])
    const reason = info?.manualOnly ? MANUAL_NOTE : PENDING_NOTE
    return { ...tile, info: `${reason}。${tile.info}` }
  })
}
