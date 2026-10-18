import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, describe, expect, it, vi } from "vitest"

// ECharts 依赖 canvas，happy-dom 下只验证取数与读数，不画图。
vi.mock("../../src/charts", () => ({
  VChart: defineComponent({
    name: "VChart",
    props: { option: { type: Object, default: () => ({}) } },
    render: () => h("div", { class: "chart-stub" }),
  }),
}))

import CustomAnalysisView from "../../src/views/CustomAnalysisView.vue"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-12345678" },
  })
}

function point(day: string, pcr: number) {
  return {
    trade_date: day,
    call_volume: 1000,
    put_volume: Math.round(pcr * 1000),
    call_oi: 2000,
    put_oi: 1800,
    volume_pcr: pcr,
    volume_pcr_ma10: pcr,
    oi_pcr: 0.9,
    quality_flag: "ok",
  }
}

function pcrBody(group: string, name: string) {
  const latest = { ...point("2026-10-16", 0.55), sentiment_signal: "极度贪婪" }
  return {
    data: {
      group_code: group,
      group_name: name,
      index_code: "000300",
      index_name: `${name}指数`,
      rules: ["PCR = 认沽量 / 认购量"],
      rules_version: "custom-analysis-v1",
      baskets: [
        {
          kind: "etf",
          label: "ETF 期权",
          members: ["SSE 510300"],
          note: "",
          status: "ok",
          first_date: "2026-10-15",
          last_date: "2026-10-16",
          history_years: 0.01,
          latest,
          points: [point("2026-10-15", 0.6), latest],
        },
        {
          kind: "index",
          label: "股指期权",
          members: ["CFFEX IO"],
          note: "",
          status: "unavailable",
          reason: "还没有同步中金所数据",
        },
      ],
    },
  }
}

const STUBS = { "el-icon": true }

async function renderAt(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/custom-analysis", component: CustomAnalysisView }],
  })
  await router.push(path)
  await router.isReady()
  render(CustomAnalysisView, { global: { plugins: [router], stubs: STUBS } })
  return router
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("自定义数据分析页", () => {
  it("默认展示期权 PCR：读数、信号与不可用篮子的原因", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/custom-analysis/pcr/csi300")) {
        return Promise.resolve(jsonResponse(pcrBody("csi300", "沪深300")))
      }
      return Promise.resolve(jsonResponse({ data: {} }, 404))
    })
    vi.stubGlobal("fetch", fetchMock)

    await renderAt("/custom-analysis")

    await waitFor(() => expect(screen.getByText("沪深300 · ETF 期权")).toBeTruthy())
    // 成交量 PCR 与 10 日均线两格都是 3 位小数的 0.550
    expect(screen.getAllByText("0.550")).toHaveLength(2)
    expect(screen.getByText("0.900")).toBeTruthy() // 持仓量 PCR
    expect(screen.getAllByText("极度贪婪").length).toBeGreaterThan(0)
    expect(screen.getByText("还没有同步中金所数据")).toBeTruthy()
    // 不可用的篮子不画图也不给导出链接
    expect(document.querySelectorAll(".chart-stub").length).toBeGreaterThan(0)
    expect(document.querySelectorAll("a.csv-link")).toHaveLength(1)
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/custom-analysis/pcr/csi300")
  })

  it("切换宽基会请求对应分组，已看过的分组不重复请求", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      const group = /pcr\/([a-z0-9]+)/.exec(url)?.[1] ?? "csi300"
      return Promise.resolve(jsonResponse(pcrBody(group, group === "sse50" ? "上证50" : "沪深300")))
    })
    vi.stubGlobal("fetch", fetchMock)

    await renderAt("/custom-analysis")
    await waitFor(() => expect(screen.getByText("沪深300 · ETF 期权")).toBeTruthy())

    await fireEvent.click(screen.getByRole("tab", { name: "上证50" }))
    await waitFor(() => expect(screen.getByText("上证50 · ETF 期权")).toBeTruthy())
    await fireEvent.click(screen.getByRole("tab", { name: "沪深300" }))
    await waitFor(() => expect(screen.getByText("沪深300 · ETF 期权")).toBeTruthy())

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("科创50 标签请求 star50，只有 ETF 期权一个篮子", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/pcr/star50")) {
        const body = pcrBody("star50", "科创50")
        body.data.baskets = body.data.baskets.slice(0, 1)
        return Promise.resolve(jsonResponse(body))
      }
      return Promise.resolve(jsonResponse(pcrBody("csi300", "沪深300")))
    })
    vi.stubGlobal("fetch", fetchMock)

    await renderAt("/custom-analysis")
    await waitFor(() => expect(screen.getByText("沪深300 · ETF 期权")).toBeTruthy())

    await fireEvent.click(screen.getByRole("tab", { name: "科创50" }))

    await waitFor(() => expect(screen.getByText("科创50 · ETF 期权")).toBeTruthy())
    expect(screen.queryByText("还没有同步中金所数据")).toBeNull()
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain("/custom-analysis/pcr/star50")
  })

  it("接口失败时显示错误并允许重试，不展示任何旧数字", async () => {
    let calls = 0
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        calls += 1
        return Promise.resolve(
          calls === 1
            ? jsonResponse({ error: { code: "DATABASE_UNAVAILABLE", message: "数据库未就绪" } }, 503)
            : jsonResponse(pcrBody("csi300", "沪深300")),
        )
      }),
    )

    await renderAt("/custom-analysis")
    await waitFor(() => expect(screen.getByText(/数据库未就绪/)).toBeTruthy())
    expect(screen.queryAllByText("0.550")).toHaveLength(0)

    await fireEvent.click(screen.getByRole("button", { name: /重试/ }))
    await waitFor(() => expect(screen.getByText("沪深300 · ETF 期权")).toBeTruthy())
  })

  it("?tab=valuation 直达估值页：不可用的指标写明原因，过期的指标有过期提示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            data: {
              rules_version: "custom-analysis-v1",
              indicators: [
                {
                  code: "erp",
                  name: "股债收益差 ERP",
                  unit: "百分点",
                  definition: "ERP = 100 / PE − 10Y",
                  status: "stale",
                  reason: "最近数据是 2026-09-01，已过期，请检查同步任务",
                  zone: "通道内",
                  latest: { trade_date: "2026-09-01", value: 3.1, quality_flag: "ok", extras: { pe_ttm: 14.4 } },
                  points: [{ trade_date: "2026-09-01", value: 3.1, quality_flag: "ok" }],
                },
                {
                  code: "buffett",
                  name: "巴菲特指标（A 股修正版）",
                  unit: "%",
                  definition: "总市值 / GDP",
                  status: "unavailable",
                  reason: "缺少 A 股总市值或 GDP 数据，请先运行数据同步",
                },
              ],
            },
          }),
        ),
      ),
    )

    await renderAt("/custom-analysis?tab=valuation")

    await waitFor(() => expect(screen.getByText(/股债收益差 ERP/)).toBeTruthy())
    expect(screen.getByText(/最近数据是 2026-09-01，已过期/)).toBeTruthy()
    expect(screen.getByText("缺少 A 股总市值或 GDP 数据，请先运行数据同步")).toBeTruthy()
    expect(screen.getByRole("tab", { name: "估值水位" }).getAttribute("aria-selected")).toBe("true")
  })

  it("数据源页展示待审行数与近期质量问题", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            data: {
              rules_version: "custom-analysis-v1",
              sources: [
                {
                  key: "sse_options",
                  label: "上交所期权日统计",
                  provider: "SSE",
                  url: "http://www.sse.com.cn/",
                  frequency: "日频",
                  status: "ok",
                  rows: 700,
                  first_date: "2023-10-16",
                  last_date: "2026-10-16",
                  review_rows: 3,
                },
              ],
              recent_issues: [{ trade_date: "2026-10-14", subject: "SSE 510050", note: "与交易所公布的 PCR 不一致" }],
            },
          }),
        ),
      ),
    )

    await renderAt("/custom-analysis?tab=sources")

    await waitFor(() => expect(screen.getByText("上交所期权日统计")).toBeTruthy())
    expect(screen.getByText("SSE 510050")).toBeTruthy()
    expect(screen.getByText("与交易所公布的 PCR 不一致")).toBeTruthy()
    expect(screen.getByText("正常")).toBeTruthy()
  })

  it("数据源为空时显示空状态而不是空表", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ data: { sources: [], recent_issues: [], rules_version: "v1" } }))),
    )

    await renderAt("/custom-analysis?tab=sources")

    await waitFor(() => expect(screen.getByText("还没有登记任何数据源")).toBeTruthy())
    expect(screen.getByText("最近没有被标记的数据")).toBeTruthy()
  })
})
