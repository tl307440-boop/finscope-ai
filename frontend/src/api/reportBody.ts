import { conceptAmount, conceptLabel, unitLabel } from "./secConcepts"

/**
 * 研究记录正文是后端按行拼好的纯文本（backend/app/services/filing_report.py）：
 *   第 1 行标题，随后是口径说明，「报告期 A 与 B」，
 *   每个指标一行「概念 单位 早期 x 申报号 a 来源 u 事实 id 后期 … 变化 n」，
 *   最后是文档引用（或「没有可引用的上传文档。」）与模型补充的结论。
 * 这里只做拆分与中文化，不推算任何变化率。
 */

export interface ReportSide {
  value: number | null
  accession: string
  sourceUrl: string
}

export interface ReportMetric {
  key: string
  label: string
  unit: string
  unitName: string
  earlier: ReportSide | null
  later: ReportSide | null
  /** 后端给出的变化值；「无法计算」时为 null */
  change: string | null
  reason: string
}

export interface ReportSource {
  accession: string
  sourceUrl: string
}

export interface ReportBody {
  heading: string
  notes: string[]
  periods: { earlier: string; later: string } | null
  metrics: ReportMetric[]
  documents: string[]
  documentNote: string
  extra: string[]
  sources: ReportSource[]
}

const PERIODS = /^报告期 (\S+) 与 (\S+)$/
const METRIC = /^(\S+) (\S+) (早期 .+?) (后期 .+?) 变化 (\S+)(?: 变化原因 (.+))?$/
const SIDE = /^(?:早期|后期) (?:暂无|(-?\d+(?:\.\d+)?) 申报号 (\S+) 来源 (\S+)(?: 事实 \S+)?)$/
const NO_DOCUMENT = "没有可引用的上传文档。"

function readSide(chunk: string): ReportSide | null {
  const match = SIDE.exec(chunk)
  if (!match || match[1] === undefined) {
    return null
  }
  const value = Number(match[1])
  return {
    value: Number.isFinite(value) ? value : null,
    accession: match[2] ?? "",
    sourceUrl: match[3] ?? "",
  }
}

export function parseReportBody(body: string): ReportBody {
  const parsed: ReportBody = {
    heading: "",
    notes: [],
    periods: null,
    metrics: [],
    documents: [],
    documentNote: "",
    extra: [],
    sources: [],
  }
  const seenSources = new Set<string>()
  const lines = body
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  lines.forEach((line, index) => {
    if (index === 0) {
      parsed.heading = line
      return
    }
    const periods = PERIODS.exec(line)
    if (periods) {
      parsed.periods = { earlier: periods[1] ?? "", later: periods[2] ?? "" }
      return
    }
    const metric = METRIC.exec(line)
    if (metric) {
      const unit = metric[2] ?? ""
      const earlier = readSide(metric[3] ?? "")
      const later = readSide(metric[4] ?? "")
      for (const side of [earlier, later]) {
        if (side?.accession && !seenSources.has(side.accession)) {
          seenSources.add(side.accession)
          parsed.sources.push({ accession: side.accession, sourceUrl: side.sourceUrl })
        }
      }
      parsed.metrics.push({
        key: `${metric[1]}-${parsed.metrics.length}`,
        label: conceptLabel(metric[1] ?? ""),
        unit,
        unitName: unitLabel(unit),
        earlier,
        later,
        change: metric[5] && metric[5] !== "无法计算" ? metric[5] : null,
        reason: metric[6] ?? "",
      })
      return
    }
    if (line === NO_DOCUMENT) {
      parsed.documentNote = line
      return
    }
    if (line.startsWith("文档 ")) {
      parsed.documents.push(line)
      return
    }
    const target = parsed.metrics.length ? parsed.extra : parsed.notes
    if (!target.includes(line)) {
      target.push(line)
    }
  })
  return parsed
}

export function sideText(side: ReportSide | null, unit: string): string {
  if (!side) {
    return "暂无"
  }
  const amount = conceptAmount(side.value, unit)
  return amount.text === "—" ? "暂无" : `${amount.text} ${amount.unit}`.trim()
}

/** 柱状图只放金额类（单位 USD/CNY）且两期都有数值的指标，每股收益量纲不同不混画。 */
export function chartableMetrics(metrics: ReportMetric[], limit = 8): ReportMetric[] {
  return metrics
    .filter(
      (metric) =>
        /^[A-Z]{3}$/.test(metric.unit) &&
        metric.earlier?.value != null &&
        metric.later?.value != null,
    )
    .slice(0, limit)
}

/** 同一指标出现两次（例如两种营业收入口径）时只留第一条，避免 KPI 重复。 */
export function headlineMetrics(metrics: ReportMetric[], limit = 4): ReportMetric[] {
  const order = ["营业收入", "净利润", "经营活动现金流", "毛利", "营业利润", "总资产"]
  const picked: ReportMetric[] = []
  for (const label of order) {
    const hit = metrics.find((metric) => metric.label === label && metric.later?.value != null)
    if (hit && !picked.some((item) => item.label === hit.label)) {
      picked.push(hit)
    }
    if (picked.length >= limit) {
      break
    }
  }
  return picked
}
