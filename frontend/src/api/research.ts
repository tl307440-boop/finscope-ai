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
