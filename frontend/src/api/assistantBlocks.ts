/**
 * 助手回答里的步骤与渲染块（规格 §9）。纯函数，便于离线测试。
 *
 * 块全部由服务端根据已入库的数据生成，数字不经过模型；这里只做收窄和展示用的整理，
 * 不重新计算、不补数。字段缺失或类型不对的块直接丢弃，而不是猜着显示。
 */
import { asList, asNumber, asRecord, asString } from "./json"

export type StepStatus = "running" | "ok" | "stale" | "unavailable"

export interface AssistantStep {
  id: string
  label: string
  status: StepStatus
}

export interface BlockColumn {
  key: string
  label: string
  unit: string
}

export type Cell = string | null
export type BlockRow = Record<string, Cell>

export interface LineChartBlock {
  type: "line_chart"
  evidence: string
  title: string
  x: string
  series: BlockColumn[]
  rows: BlockRow[]
}

export interface MetricItem {
  label: string
  value: Cell
  unit: string
  asOf: string
  status: string
  change: Cell
  changeLabel: string
}

export interface MetricCardsBlock {
  type: "metric_cards"
  evidence: string
  items: MetricItem[]
}

export interface TableBlock {
  type: "table"
  evidence: string
  title: string
  columns: BlockColumn[]
  rows: BlockRow[]
  truncated: number
}

export interface NoticeBlock {
  type: "notice"
  level: "info" | "warning"
  code: string
  message: string
}

export type AssistantBlock = LineChartBlock | MetricCardsBlock | TableBlock | NoticeBlock

/** 一条回答带的附加内容：流式过程中逐个事件累加，历史消息一次读出。 */
export interface ReplyExtras {
  steps: AssistantStep[]
  blocks: AssistantBlock[]
  asOf: string
  grounding: string
}

export const EMPTY_EXTRAS: ReplyExtras = { steps: [], blocks: [], asOf: "", grounding: "" }

const STEP_STATUS: readonly StepStatus[] = ["running", "ok", "stale", "unavailable"]

/** 核对没通过时服务端给的提示：它说明的是上面那段文字，所以放在正文后面。 */
export const NOT_GROUNDED = "NOT_GROUNDED"

function cell(value: unknown): Cell {
  if (typeof value === "string") {
    return value === "" ? null : value
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value)
  }
  return null
}

function columns(value: unknown): BlockColumn[] {
  return asList(value).flatMap((item) => {
    const record = asRecord(item)
    const key = asString(record?.key)
    if (!record || !key) {
      return []
    }
    return [{ key, label: asString(record.label) ?? key, unit: asString(record.unit) ?? "" }]
  })
}

function rows(value: unknown): BlockRow[] {
  return asList(value).flatMap((item) => {
    const record = asRecord(item)
    if (!record) {
      return []
    }
    const row: BlockRow = {}
    for (const [key, raw] of Object.entries(record)) {
      row[key] = cell(raw)
    }
    return [row]
  })
}

export function parseStep(value: unknown): AssistantStep | null {
  const record = asRecord(value)
  const id = asString(record?.id)
  const label = asString(record?.label)
  const status = STEP_STATUS.find((item) => item === record?.status)
  if (!record || !id || !label || !status) {
    return null
  }
  return { id, label, status }
}

export function parseSteps(value: unknown): AssistantStep[] {
  return asList(value).flatMap((item) => {
    const step = parseStep(item)
    return step ? [step] : []
  })
}

export function parseBlock(value: unknown): AssistantBlock | null {
  const record = asRecord(value)
  if (!record) {
    return null
  }
  const evidence = asString(record.evidence) ?? ""
  switch (record.type) {
    case "line_chart": {
      const x = asString(record.x)
      const series = columns(record.series)
      if (!x || !series.length) {
        return null
      }
      return {
        type: "line_chart",
        evidence,
        title: asString(record.title) ?? "",
        x,
        series,
        rows: rows(record.rows),
      }
    }
    case "metric_cards": {
      const items = asList(record.items).flatMap((raw): MetricItem[] => {
        const item = asRecord(raw)
        const label = asString(item?.label)
        if (!item || !label) {
          return []
        }
        return [
          {
            label,
            value: cell(item.value),
            unit: asString(item.unit) ?? "",
            asOf: asString(item.as_of) ?? "",
            status: asString(item.status) ?? "ok",
            change: cell(item.change),
            changeLabel: asString(item.change_label) ?? "",
          },
        ]
      })
      return items.length ? { type: "metric_cards", evidence, items } : null
    }
    case "table": {
      const cols = columns(record.columns)
      if (!cols.length) {
        return null
      }
      return {
        type: "table",
        evidence,
        title: asString(record.title) ?? "",
        columns: cols,
        rows: rows(record.rows),
        truncated: Math.max(0, Math.trunc(asNumber(record.truncated) ?? 0)),
      }
    }
    case "notice": {
      const message = asString(record.message)
      if (!message) {
        return null
      }
      return {
        type: "notice",
        level: record.level === "warning" ? "warning" : "info",
        code: asString(record.code) ?? "",
        message,
      }
    }
    default:
      return null
  }
}

export function parseBlocks(value: unknown): AssistantBlock[] {
  return asList(value).flatMap((item) => {
    const block = parseBlock(item)
    return block ? [block] : []
  })
}

/** 历史消息里的附加内容；旧消息没有这些字段，就是空。 */
export function extrasFromMessage(record: Record<string, unknown>): ReplyExtras {
  return {
    steps: parseSteps(record.steps),
    blocks: parseBlocks(record.blocks),
    asOf: asString(record.as_of) ?? "",
    grounding: asString(record.grounding) ?? "",
  }
}

function withStep(steps: AssistantStep[], step: AssistantStep): AssistantStep[] {
  const at = steps.findIndex((item) => item.id === step.id)
  if (at < 0) {
    return [...steps, step]
  }
  return steps.map((item, index) => (index === at ? step : item))
}

/** 一个流式事件并入附加内容。不认识的事件原样返回，旧协议和以后新增的事件都不会弄坏它。 */
export function applyEvent(extras: ReplyExtras, payload: Record<string, unknown>): ReplyExtras {
  switch (payload.type) {
    case "step": {
      const step = parseStep(payload)
      return step ? { ...extras, steps: withStep(extras.steps, step) } : extras
    }
    case "block": {
      const block = parseBlock(payload.block)
      return block ? { ...extras, blocks: [...extras.blocks, block] } : extras
    }
    case "done":
      return {
        ...extras,
        asOf: asString(payload.as_of) ?? extras.asOf,
        grounding: asString(payload.grounding) ?? extras.grounding,
      }
    default:
      return extras
  }
}

/** 正文之后的核对提示，和跟着数据走的其他块分开。 */
export function splitNotices(blocks: AssistantBlock[]): {
  verdict: NoticeBlock[]
  data: AssistantBlock[]
} {
  const verdict: NoticeBlock[] = []
  const data: AssistantBlock[] = []
  for (const block of blocks) {
    if (block.type === "notice" && block.code === NOT_GROUNDED) {
      verdict.push(block)
    } else {
      data.push(block)
    }
  }
  return { verdict, data }
}

export function stepSummary(steps: AssistantStep[]): string {
  if (!steps.length) {
    return ""
  }
  const running = steps.filter((step) => step.status === "running").length
  if (running) {
    return `正在查数据（${steps.length - running}/${steps.length}）`
  }
  const missing = steps.filter((step) => step.status === "unavailable").length
  return missing ? `查了 ${steps.length} 项数据，其中 ${missing} 项没有数据` : `查了 ${steps.length} 项数据`
}

/** 折线图配置。取不到数值的点留空，不用 0 顶替；整条都没有数值就不画。 */
export function lineChartOption(block: LineChartBlock): Record<string, unknown> | null {
  const categories = block.rows.map((row) => row[block.x] ?? "")
  const series = block.series.flatMap((item) => {
    const data = block.rows.map((row) => asNumber(row[item.key]))
    if (!data.some((value) => value !== null)) {
      return []
    }
    return [
      {
        name: item.unit ? `${item.label}（${item.unit}）` : item.label,
        type: "line",
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 2 },
        data,
      },
    ]
  })
  if (!series.length || categories.length < 2) {
    return null
  }
  return {
    grid: { left: 44, right: 12, top: 30, bottom: 26 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, left: 0 },
    xAxis: { type: "category", data: categories, axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true },
    series,
  }
}

export function cellText(value: Cell): string {
  return value === null ? "—" : value
}
