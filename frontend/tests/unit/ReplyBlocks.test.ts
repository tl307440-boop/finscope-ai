import { mount } from "@vue/test-utils"
import { describe, expect, it, vi } from "vitest"
import { parseBlocks, parseSteps } from "../../src/api/assistantBlocks"
import ReplyBlocks from "../../src/components/assistant/ReplyBlocks.vue"
import ReplySteps from "../../src/components/assistant/ReplySteps.vue"

// 图表本身（ECharts 画布）在 jsdom 里没有意义，换成一个能看到配置的占位。
vi.mock("../../src/charts", () => ({
  VChart: {
    props: ["option"],
    template: '<div class="vchart" :data-series="option.series.length"></div>',
  },
}))

const blocks = parseBlocks([
  {
    type: "metric_cards",
    evidence: "E1",
    items: [{ label: "美国 CPI", value: "323.0", unit: "指数", as_of: "2026-09", status: "ok", change: "1.6", change_label: "较上一期" }],
  },
  {
    type: "line_chart",
    evidence: "E1",
    title: "美国 CPI",
    x: "date",
    series: [{ key: "value", label: "CPI", unit: "指数" }],
    rows: [
      { date: "2026-07", value: "320.1" },
      { date: "2026-08", value: "321.4" },
      { date: "2026-09", value: "323.0" },
    ],
  },
  {
    type: "table",
    evidence: "E2",
    title: "沪深300期权认沽认购比",
    columns: [
      { key: "basket", label: "合约组", unit: "" },
      { key: "volume_pcr", label: "成交量 PCR", unit: "" },
    ],
    rows: [
      { basket: "沪深300ETF期权", volume_pcr: "0.8512" },
      { basket: "沪深300股指期权 IO", volume_pcr: null },
    ],
    truncated: 3,
  },
  { type: "notice", level: "warning", code: "STALE", message: "期权数据偏旧" },
])

describe("ReplyBlocks 渲染块", () => {
  it("指标卡、折线图、表格和提示都画出来，数字原样显示", () => {
    const wrapper = mount(ReplyBlocks, { props: { blocks } })
    expect(wrapper.text()).toContain("323.0")
    expect(wrapper.text()).toContain("较上一期")
    expect(wrapper.find(".vchart").exists()).toBe(true)
    expect(wrapper.find(".vchart").attributes("data-series")).toBe("1")
    const cells = wrapper.findAll("tbody td").map((cell) => cell.text())
    expect(cells).toEqual(["沪深300ETF期权", "0.8512", "沪深300股指期权 IO", "—"])
    expect(wrapper.find(".notice").text()).toBe("期权数据偏旧")
    expect(wrapper.find(".notice").attributes("data-level")).toBe("warning")
  })

  it("表格底部写明行数，没取完时说出来", () => {
    const wrapper = mount(ReplyBlocks, { props: { blocks } })
    const foot = wrapper.find(".foot").text()
    expect(foot).toContain("共 2 行")
    expect(foot).toContain("没有取完")
  })

  it("数据点不够时不画图，也不报错", () => {
    const thin = parseBlocks([
      {
        type: "line_chart",
        title: "只有一个点",
        x: "date",
        series: [{ key: "value", label: "CPI", unit: "" }],
        rows: [{ date: "2026-09", value: "323.0" }],
      },
    ])
    const wrapper = mount(ReplyBlocks, { props: { blocks: thin } })
    expect(wrapper.find(".vchart").exists()).toBe(false)
    expect(wrapper.text()).toContain("数据点不够")
  })

  it("没有块就什么也不渲染", () => {
    const wrapper = mount(ReplyBlocks, { props: { blocks: [] } })
    expect(wrapper.find(".blocks").exists()).toBe(false)
  })

  it("资料里带尖括号的文字只会当文字显示，不会变成标签", () => {
    const hostile = parseBlocks([
      {
        type: "table",
        title: "t",
        columns: [{ key: "name", label: "名称", unit: "" }],
        rows: [{ name: "<img src=x onerror=alert(1)>" }],
        truncated: 0,
      },
    ])
    const wrapper = mount(ReplyBlocks, { props: { blocks: hostile } })
    expect(wrapper.find("td img").exists()).toBe(false)
    expect(wrapper.find("td").text()).toBe("<img src=x onerror=alert(1)>")
  })
})

describe("ReplySteps 步骤列表", () => {
  it("没有步骤不渲染；有未完成的步骤时展开，查完后收起", () => {
    expect(mount(ReplySteps, { props: { steps: [] } }).find("details").exists()).toBe(false)

    const running = parseSteps([
      { id: "s1", label: "查询 CPI：美国 CPI", status: "ok" },
      { id: "s2", label: "查询期权 PCR…", status: "running" },
    ])
    const open = mount(ReplySteps, { props: { steps: running } })
    expect(open.find("summary").text()).toBe("正在查数据（1/2）")
    expect(open.find("details").attributes("open")).toBeDefined()

    const done = parseSteps([
      { id: "s1", label: "查询 CPI：美国 CPI", status: "ok" },
      { id: "s2", label: "查询期权 PCR：沪深300", status: "unavailable" },
    ])
    const closed = mount(ReplySteps, { props: { steps: done } })
    expect(closed.find("details").attributes("open")).toBeUndefined()
    expect(closed.find("summary").text()).toBe("查了 2 项数据，其中 1 项没有数据")
    expect(closed.findAll("li").map((item) => item.attributes("data-status"))).toEqual(["ok", "unavailable"])
    expect(closed.text()).toContain("没有数据")
  })
})
