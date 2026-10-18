import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.mock("../../src/charts", () => ({
  VChart: defineComponent({ name: "VChart", render: () => h("div", { class: "chart-stub" }) }),
}))

import MacroAiPanel from "../../src/views/parts/macro/MacroAiPanel.vue"
import MacroComparePanel from "../../src/views/parts/macro/MacroComparePanel.vue"
import MacroScenarioPanel from "../../src/views/parts/macro/MacroScenarioPanel.vue"
import { buildQuadrantModel, quarterLastMonth } from "../../src/api/macroStats"
import { useSessionStore } from "../../src/stores/auth"
import type { CompareSpec } from "../../src/api/macroCompare"

const STUBS = { "router-link": true }

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-12345678" },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function quarterDate(index: number): string {
  return `${2020 + Math.floor(index / 4)}-${String(1 + 3 * (index % 4)).padStart(2, "0")}-01`
}

function modelOf(count: number) {
  const growth = Array.from({ length: count }, (_, index) => ({ date: quarterDate(index), value: 5 }))
  const inflation = Array.from({ length: count }, (_, index) => ({
    date: `${quarterLastMonth(quarterDate(index))}-01`,
    value: index % 2 === 0 ? 2 : 0,
  }))
  return buildQuadrantModel(growth, inflation, 30)
}

describe("情景推演面板", () => {
  it("样本充足：显示历史次数、占比与分位区间，并声明不是预测", () => {
    render(MacroScenarioPanel, { props: { model: modelOf(24), growthLabel: "增长", inflationLabel: "通胀" } })
    expect(screen.getByText("未来宏观行情推演")).toBeTruthy()
    expect(document.body.textContent).toContain("11 / 11")
    expect(document.body.textContent).toContain("100%")
    expect(document.body.textContent).toContain("下一季度历史分位区间")
    expect(document.body.textContent).toContain("不是对未来的预测")
    // 参考图里没有出处的概率不会出现
    expect(document.body.textContent).not.toMatch(/50%.*30%.*20%/)
  })

  it("样本不足：只给次数，不给百分比和区间", () => {
    render(MacroScenarioPanel, { props: { model: modelOf(6), growthLabel: "增长", inflationLabel: "通胀" } })
    expect(document.body.textContent).toContain("样本不足")
    expect(document.body.textContent).not.toContain("100%")
    expect(document.body.textContent).toContain("不给区间")
  })

  it("没有象限模型时提示无法推演", () => {
    render(MacroScenarioPanel, { props: { model: null, growthLabel: "增长", inflationLabel: "通胀" } })
    expect(document.body.textContent).toContain("无法做历史统计推演")
  })
})

const SPECS: CompareSpec[] = [
  { code: "CPI", label: "CPI", unit: "指数", indexable: true, tone: "accent" },
  { code: "RATE", label: "失业率", unit: "%", indexable: false, tone: "violet" },
]
const ROWS = {
  CPI: [
    { date: "2020-01-01", value: 200 },
    { date: "2026-09-01", value: 300 },
  ],
  RATE: [
    { date: "2020-01-01", value: 3.6 },
    { date: "2026-09-01", value: 4.3 },
  ],
}

describe("对比分析面板", () => {
  it("指数化视图：不可指数化的序列给出原因；导出 CSV 触发下载", async () => {
    const createObjectURL = vi.fn(() => "blob:test")
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined)
    render(MacroComparePanel, {
      props: { specs: SPECS, rowsByCode: ROWS, defaultCodes: ["CPI", "RATE"], filename: "demo", defaultMode: "index" },
    })
    expect(document.body.textContent).toContain("失业率：不是水平值")
    await fireEvent.click(screen.getByText("导出 CSV"))
    expect(createObjectURL).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
  })

  it("没有可指数化的序列时禁用指数化按钮；没有数据时导出按钮禁用", () => {
    render(MacroComparePanel, {
      props: {
        specs: [SPECS[1] as CompareSpec],
        rowsByCode: {},
        defaultCodes: ["RATE"],
        filename: "demo",
      },
    })
    expect((screen.getByText("指数化") as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByText("导出 CSV") as HTMLButtonElement).disabled).toBe(true)
    expect(document.body.textContent).toContain("没有可画的数据")
  })
})

function aiBody(status: string, extra: Record<string, unknown> = {}) {
  return {
    data: {
      region: "cn",
      pit_date: "2026-10-18",
      status,
      message: status === "ok" ? "已通过数字核对" : "提示",
      text: null,
      model: null,
      notice: "模型生成内容，仅用于研究与教学，不构成投资建议",
      facts: [
        {
          code: "PPI_CN",
          label: "PPI同比",
          unit: "%",
          period: "2026-09-01",
          value: "5.00",
          previous_period: "2026-08-01",
          previous_value: "4.60",
          change: "+0.40",
        },
      ],
      ...extra,
    },
  }
}

describe("AI 宏观解读面板", () => {
  it("未登录时不请求模型，提示登录", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL) => Promise.resolve(jsonResponse({}, 401)))
    vi.stubGlobal("fetch", fetchMock)
    render(MacroAiPanel, { props: { region: "cn", asOf: "2026-10-18" }, global: { stubs: STUBS } })
    await fireEvent.click(screen.getByText("生成解读"))
    await waitFor(() => expect(document.body.textContent).toContain("登录后才能使用 AI 解读"))
    expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("/macro/interpretation"))).toBe(true)
  })

  it("已登录：通过核对的文字标注模型生成，并可展开模型可用的数据", async () => {
    useSessionStore().accessToken = "token"
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(jsonResponse(aiBody("ok", { text: "PPI同比 2026-09 为 5.00%。", model: "m-1" }))),
    )
    vi.stubGlobal("fetch", fetchMock)
    render(MacroAiPanel, { props: { region: "cn", asOf: "2026-10-18" }, global: { stubs: STUBS } })
    await fireEvent.click(screen.getByText("生成解读"))
    await waitFor(() => expect(document.body.textContent).toContain("PPI同比 2026-09 为 5.00%。"))
    expect(document.body.textContent).toContain("模型生成 · m-1")
    expect(document.body.textContent).toContain("模型可使用的数据（1 项）")
    const init = fetchMock.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual({ region: "cn", as_of: "2026-10-18" })
  })

  it("数字核对未通过 / 未配置模型：不显示任何模型文字，只列出数据", async () => {
    useSessionStore().accessToken = "token"
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(jsonResponse(aiBody("rejected", { text: "不应显示的文字 12.3", message: "模型输出含有资料中没有的数字" }))),
      ),
    )
    render(MacroAiPanel, { props: { region: "cn", asOf: "2026-10-18" }, global: { stubs: STUBS } })
    await fireEvent.click(screen.getByText("生成解读"))
    await waitFor(() => expect(document.body.textContent).toContain("模型输出含有资料中没有的数字"))
    expect(document.body.textContent).not.toContain("不应显示的文字")
    expect(document.body.textContent).toContain("PPI同比")
  })

  it("接口出错时显示错误并可重试", async () => {
    useSessionStore().accessToken = "token"
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ error: { code: "RATE_LIMITED", message: "同时进行的模型任务已达上限" } }, 429))),
    )
    render(MacroAiPanel, { props: { region: "us", asOf: "2026-10-18" }, global: { stubs: STUBS } })
    await fireEvent.click(screen.getByText("生成解读"))
    expect(await screen.findByText("同时进行的模型任务已达上限")).toBeTruthy()
  })
})
