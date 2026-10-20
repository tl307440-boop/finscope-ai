import { describe, expect, it } from "vitest"
import {
  applyEvent,
  EMPTY_EXTRAS,
  extrasFromMessage,
  lineChartOption,
  parseBlock,
  parseBlocks,
  parseStep,
  splitNotices,
  stepSummary,
  type AssistantBlock,
  type LineChartBlock,
  type ReplyExtras,
} from "../../src/api/assistantBlocks"
import { parseSseLine } from "../../src/api/assistantStream"

const TABLE = {
  type: "table",
  evidence: "E1",
  title: "沪深300期权认沽认购比",
  columns: [
    { key: "basket", label: "合约组", unit: "" },
    { key: "volume_pcr", label: "成交量 PCR", unit: "" },
  ],
  rows: [
    { basket: "沪深300ETF期权", volume_pcr: "0.8512" },
    { basket: "沪深300股指期权 IO", volume_pcr: null },
  ],
  truncated: 0,
}

const CHART = {
  type: "line_chart",
  evidence: "E2",
  title: "美国 CPI",
  x: "date",
  series: [{ key: "value", label: "CPI", unit: "指数" }],
  rows: [
    { date: "2026-07", value: "320.1" },
    { date: "2026-08", value: "321.4" },
    { date: "2026-09", value: "323.0" },
  ],
}

/** 把一段 SSE 文本逐行解析后依次并入，和视图里的 readStream 做的事一样。 */
function replay(raw: string): { text: string; extras: ReplyExtras } {
  let text = ""
  let extras = EMPTY_EXTRAS
  for (const line of raw.split("\n")) {
    const payload = parseSseLine(line)
    if (!payload) {
      continue
    }
    if (typeof payload.delta === "string") {
      text += payload.delta
    }
    extras = applyEvent(extras, payload)
  }
  return { text, extras }
}

describe("步骤与块的解析", () => {
  it("步骤要有编号、文字和已知状态，其余丢弃", () => {
    expect(parseStep({ id: "s1", label: "查询 CPI", status: "ok" })).toEqual({
      id: "s1",
      label: "查询 CPI",
      status: "ok",
    })
    expect(parseStep({ id: "s1", label: "x", status: "weird" })).toBeNull()
    expect(parseStep({ label: "x", status: "ok" })).toBeNull()
    expect(parseStep("s1")).toBeNull()
  })

  it("表格：单元格只收字符串和数字，空值显示为 null，不补数", () => {
    const block = parseBlock(TABLE)
    expect(block?.type).toBe("table")
    if (block?.type !== "table") {
      return
    }
    expect(block.rows[0]).toEqual({ basket: "沪深300ETF期权", volume_pcr: "0.8512" })
    expect(block.rows[1]?.volume_pcr).toBeNull()
    expect(block.truncated).toBe(0)
  })

  it("没有列的表格、没有 x 的折线图、没有内容的提示都是废块", () => {
    expect(parseBlock({ ...TABLE, columns: [] })).toBeNull()
    expect(parseBlock({ ...CHART, x: "" })).toBeNull()
    expect(parseBlock({ ...CHART, series: [] })).toBeNull()
    expect(parseBlock({ type: "notice", level: "warning", code: "X", message: "" })).toBeNull()
    expect(parseBlock({ type: "metric_cards", items: [] })).toBeNull()
  })

  it("不认识的块类型和不是对象的值被忽略，不会抛错", () => {
    expect(parseBlock({ type: "radar" })).toBeNull()
    expect(parseBlock(null)).toBeNull()
    expect(parseBlock("table")).toBeNull()
    expect(parseBlocks([TABLE, { type: "radar" }, 3, CHART]).map((block) => block.type)).toEqual([
      "table",
      "line_chart",
    ])
  })

  it("指标卡的涨跌是可选的", () => {
    const block = parseBlock({
      type: "metric_cards",
      evidence: "E2",
      items: [
        { label: "美国 CPI", value: "323.0", unit: "指数", as_of: "2026-09", status: "ok" },
        { label: "失业率", value: "4.1", change: "-0.1", change_label: "较上一期" },
      ],
    })
    expect(block?.type).toBe("metric_cards")
    if (block?.type !== "metric_cards") {
      return
    }
    expect(block.items[0]?.change).toBeNull()
    expect(block.items[1]).toMatchObject({ change: "-0.1", changeLabel: "较上一期" })
  })

  it("提示的级别只认 warning，其余按 info", () => {
    const warn = parseBlock({ type: "notice", level: "warning", code: "STALE", message: "偏旧" })
    const other = parseBlock({ type: "notice", level: "error", code: "X", message: "m" })
    expect(warn).toMatchObject({ level: "warning" })
    expect(other).toMatchObject({ level: "info" })
  })
})

describe("历史消息", () => {
  it("旧消息没有这些字段，读出来就是空的", () => {
    expect(extrasFromMessage({ id: "1", role: "assistant", content: "旧回答" })).toEqual(EMPTY_EXTRAS)
  })

  it("新消息读出步骤、块、截止日和核对结论", () => {
    const extras = extrasFromMessage({
      steps: [{ id: "s1", label: "查询", status: "ok" }, { bad: true }],
      blocks: [TABLE],
      as_of: "2026-10-20",
      grounding: "partial",
    })
    expect(extras.steps).toHaveLength(1)
    expect(extras.blocks).toHaveLength(1)
    expect(extras.asOf).toBe("2026-10-20")
    expect(extras.grounding).toBe("partial")
  })
})

describe("流式事件并入", () => {
  it("同一个步骤先 running 后变终态，只留一条", () => {
    let extras = applyEvent(EMPTY_EXTRAS, { type: "step", id: "s1", label: "查询…", status: "running" })
    extras = applyEvent(extras, { type: "step", id: "s2", label: "再查…", status: "running" })
    extras = applyEvent(extras, { type: "step", id: "s1", label: "查询：CPI", status: "ok" })
    expect(extras.steps).toEqual([
      { id: "s1", label: "查询：CPI", status: "ok" },
      { id: "s2", label: "再查…", status: "running" },
    ])
  })

  it("整段回合：块先到、正文后到，done 带来截止日和核对结论", () => {
    const raw =
      'data: {"type":"step","id":"s1","label":"查询期权 PCR…","status":"running"}\n\n' +
      'data: {"type":"step","id":"s1","label":"查询期权 PCR：沪深300","status":"ok"}\n\n' +
      `data: ${JSON.stringify({ type: "block", block: TABLE })}\n\n` +
      'data: {"type":"delta","delta":"最新数据见下表。"}\n\n' +
      ": ping\n\n" +
      'data: {"type":"done","done":true,"status":"completed","as_of":"2026-10-20","grounding":"full"}\n\n'
    const { text, extras } = replay(raw)
    expect(text).toBe("最新数据见下表。")
    expect(extras.steps).toHaveLength(1)
    expect(extras.blocks.map((block) => block.type)).toEqual(["table"])
    expect(extras.asOf).toBe("2026-10-20")
    expect(extras.grounding).toBe("full")
  })

  it("旧协议（只有 delta 与 done）原样通过，附加内容保持为空", () => {
    const raw =
      'data: {"type":"delta","delta":"你好"}\n\n' + 'data: {"type":"done","done":true,"status":"completed"}\n\n'
    const { text, extras } = replay(raw)
    expect(text).toBe("你好")
    expect(extras).toEqual(EMPTY_EXTRAS)
  })

  it("坏事件不改动已有内容：返回同一个对象", () => {
    const before = applyEvent(EMPTY_EXTRAS, { type: "step", id: "s1", label: "查", status: "ok" })
    expect(applyEvent(before, { type: "block", block: { type: "radar" } })).toBe(before)
    expect(applyEvent(before, { type: "step", id: "s2" })).toBe(before)
    expect(applyEvent(before, { type: "something_new" })).toBe(before)
    expect(applyEvent(before, { type: "delta", delta: "x" })).toBe(before)
  })

  it("并入不改写原对象", () => {
    const next = applyEvent(EMPTY_EXTRAS, { type: "block", block: TABLE })
    expect(EMPTY_EXTRAS.blocks).toHaveLength(0)
    expect(next.blocks).toHaveLength(1)
  })
})

describe("核对提示与摘要", () => {
  const blocks = parseBlocks([
    TABLE,
    { type: "notice", level: "warning", code: "NOT_GROUNDED", message: "后续内容没能通过核对，已隐藏。" },
    { type: "notice", level: "warning", code: "STALE", message: "数据偏旧" },
  ])

  it("NOT_GROUNDED 说明的是正文，单独拿出来；跟着数据走的提示留在数据块里", () => {
    const { verdict, data } = splitNotices(blocks)
    expect(verdict.map((block) => block.code)).toEqual(["NOT_GROUNDED"])
    expect(data.map((block: AssistantBlock) => block.type)).toEqual(["table", "notice"])
  })

  it("步骤摘要：查询中、已完成、有的没数据", () => {
    expect(stepSummary([])).toBe("")
    expect(
      stepSummary([
        { id: "s1", label: "a", status: "ok" },
        { id: "s2", label: "b", status: "running" },
      ]),
    ).toBe("正在查数据（1/2）")
    expect(stepSummary([{ id: "s1", label: "a", status: "ok" }])).toBe("查了 1 项数据")
    expect(
      stepSummary([
        { id: "s1", label: "a", status: "ok" },
        { id: "s2", label: "b", status: "unavailable" },
      ]),
    ).toBe("查了 2 项数据，其中 1 项没有数据")
  })
})

describe("折线图配置", () => {
  function chart(rows: LineChartBlock["rows"]): LineChartBlock {
    const block = parseBlock({ ...CHART, rows })
    if (block?.type !== "line_chart") {
      throw new Error("not a chart")
    }
    return block
  }

  it("按行生成横轴和数值，单位写进系列名", () => {
    const option = lineChartOption(chart(CHART.rows))
    expect(option).not.toBeNull()
    const series = option?.series as { name: string; data: (number | null)[] }[]
    expect(series[0]?.name).toBe("CPI（指数）")
    expect(series[0]?.data).toEqual([320.1, 321.4, 323.0])
    expect((option?.xAxis as { data: string[] }).data).toEqual(["2026-07", "2026-08", "2026-09"])
  })

  it("缺失的点留空而不是画成 0", () => {
    const option = lineChartOption(
      chart([
        { date: "2026-07", value: "320.1" },
        { date: "2026-08", value: null },
        { date: "2026-09", value: "323.0" },
      ]),
    )
    const series = option?.series as { data: (number | null)[] }[]
    expect(series[0]?.data).toEqual([320.1, null, 323.0])
  })

  it("整条没有数值或不足两个点就不画", () => {
    expect(
      lineChartOption(
        chart([
          { date: "2026-07", value: null },
          { date: "2026-08", value: null },
        ]),
      ),
    ).toBeNull()
    expect(lineChartOption(chart([{ date: "2026-07", value: "1" }]))).toBeNull()
  })
})
