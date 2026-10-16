export interface ResearchMetric {
  concept: string
  earlier: { value: string; accessionNo: string; sourceUrl: string; filed: string } | null
  later: { value: string; accessionNo: string; sourceUrl: string; filed: string } | null
  change: string | null
}

export function describeResearch(input: {
  notice: string | null
  metrics: ResearchMetric[]
  unevaluated: string[]
}): string[] {
  const lines = [input.notice ?? "没有数据时不会编造财务数字。"]
  if (input.metrics.length === 0) {
    lines.push("请选择两个不同的报告期")
    return lines
  }
  for (const metric of input.metrics) {
    const earlier = metric.earlier
      ? `${metric.earlier.value}（${metric.earlier.filed} ${metric.earlier.accessionNo}）`
      : "暂无"
    const later = metric.later
      ? `${metric.later.value}（${metric.later.filed} ${metric.later.accessionNo}）`
      : "暂无"
    const change = metric.change ? `同比 ${metric.change}` : "同比无法计算"
    lines.push(`${metric.concept} 早期 ${earlier} 后期 ${later} ${change}`)
    if (metric.later) {
      lines.push(`来源 ${metric.later.sourceUrl}`)
    }
  }
  lines.push("亮点：没有足够的可比数据，这不表示应当买入")
  lines.push("风险：没有足够的可比数据，这不表示没有风险")
  if (input.unevaluated.length) {
    lines.push(`未评估 ${input.unevaluated.join("、")}`)
  }
  return lines
}

export type RuleTone = "ok" | "warn" | "danger" | "muted" | "accent"

/** 规则引擎（backend financial_metrics.evaluate_rules）的规则名与判定口径。 */
const RULE_CATALOG: Record<string, { name: string; checks: string }> = {
  R001: { name: "应收增速快于收入", checks: "应收账款同比增速高出营业收入 20 个百分点以上时提示" },
  R002: { name: "经营现金流背离利润", checks: "经营现金流低于净利润一半时提示" },
  R003: { name: "毛利率同比下滑", checks: "毛利率同比下降超过 5 个百分点时提示" },
  R004: { name: "存货增速快于收入", checks: "存货同比增速高出营业收入 25 个百分点以上时提示" },
  R005: { name: "短期偿债压力", checks: "流动比率低于 1 且短期债务明显上升时提示" },
  R006: { name: "增长质量（积极信号）", checks: "收入同比增长超过 15%、经营现金流为正且毛利率稳定时提示" },
}

const RULE_STATUS: Record<string, { label: string; tone: RuleTone }> = {
  triggered: { label: "已触发", tone: "danger" },
  flagged: { label: "已触发", tone: "danger" },
  alert: { label: "已触发", tone: "danger" },
  clear: { label: "未触发", tone: "ok" },
  passed: { label: "未触发", tone: "ok" },
  ok: { label: "未触发", tone: "ok" },
  not_evaluated: { label: "未评估", tone: "muted" },
  insufficient_data: { label: "数据不足", tone: "warn" },
  insufficient: { label: "数据不足", tone: "warn" },
}

export function ruleName(ruleId: string): string {
  return RULE_CATALOG[ruleId]?.name ?? `规则 ${ruleId}`
}

export function ruleChecks(ruleId: string): string {
  return RULE_CATALOG[ruleId]?.checks ?? ""
}

export function ruleStatusLabel(status: string): string {
  return RULE_STATUS[status]?.label ?? (status ? "未知状态" : "—")
}

export function ruleTone(status: string): RuleTone {
  return RULE_STATUS[status]?.tone ?? "muted"
}

const RUN_STATUS: Record<string, { label: string; tone: RuleTone }> = {
  succeeded: { label: "已完成", tone: "ok" },
  completed: { label: "已完成", tone: "ok" },
  failed: { label: "生成失败", tone: "danger" },
  running: { label: "生成中", tone: "accent" },
  queued: { label: "排队中", tone: "muted" },
}

export function runStatusLabel(status: string): string {
  return RUN_STATUS[status]?.label ?? (status ? "状态未知" : "—")
}

export function runTone(status: string): RuleTone {
  return RUN_STATUS[status]?.tone ?? "muted"
}

/** 规则生成的研究记录正文里的一条指标行（见 backend filing_report._metric_line）。 */
export interface ReportMetricLine {
  concept: string
  unit: string
  earlier: string | null
  later: string | null
  earlierSource: string
  laterSource: string
  earlierAccession: string
  laterAccession: string
  change: string | null
}

export interface ParsedReport {
  periods: { earlier: string; later: string } | null
  metrics: ReportMetricLine[]
  /** 不是指标行、也不是报告期行的其它正文（标题、说明、文档引用等） */
  lines: string[]
}

const METRIC_LINE = /^(\S+:\S+) (\S+) 早期 (.+?) 后期 (.+?) 变化 (.+)$/
const PERIOD_LINE = /^报告期 (\d{4}-\d{2}-\d{2}) 与 (\d{4}-\d{2}-\d{2})$/

function parseSide(text: string): { value: string | null; source: string; accession: string } {
  if (text.trim() === "暂无") {
    return { value: null, source: "", accession: "" }
  }
  const value = text.trim().split(" ")[0] ?? ""
  const source = text.match(/来源 (https?:\/\/\S+)/)?.[1] ?? ""
  const accession = text.match(/申报号 (\S+)/)?.[1] ?? ""
  return { value: value || null, source, accession }
}

export function parseReportBody(body: string): ParsedReport {
  const parsed: ParsedReport = { periods: null, metrics: [], lines: [] }
  for (const rawLine of body.split("\n")) {
    const line = rawLine.trim()
    if (!line) {
      continue
    }
    const period = line.match(PERIOD_LINE)
    if (period) {
      parsed.periods = { earlier: period[1] ?? "", later: period[2] ?? "" }
      continue
    }
    const metric = line.match(METRIC_LINE)
    if (metric) {
      const earlier = parseSide(metric[3] ?? "")
      const later = parseSide(metric[4] ?? "")
      const change = (metric[5] ?? "").trim()
      parsed.metrics.push({
        concept: metric[1] ?? "",
        unit: metric[2] ?? "",
        earlier: earlier.value,
        later: later.value,
        earlierSource: earlier.source,
        laterSource: later.source,
        earlierAccession: earlier.accession,
        laterAccession: later.accession,
        change: change && change !== "无法计算" ? change : null,
      })
      continue
    }
    parsed.lines.push(line)
  }
  return parsed
}
