import { cleanup, render, screen, waitFor } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

// ECharts 依赖 canvas，happy-dom 下只验证页面取数与指标带，不画图。
vi.mock("../../src/charts", () => ({
  VChart: defineComponent({ name: "VChart", render: () => h("div", { class: "chart-stub" }) }),
}))

import MacroChinaView from "../../src/views/MacroChinaView.vue"
import { chinaObservationCodes } from "../../src/api/chinaPanels"

const MANUAL = ["SOCIAL_FINANCING_STOCK_CN", "ENT_MLT_LOAN_CN", "GOV_BOND_NET_CN"]

function seriesItem(code: string, withData: boolean) {
  const manual = MANUAL.includes(code)
  return {
    code,
    name: code,
    unit: "Percent",
    status: withData && !manual ? "stored" : "unavailable",
    observation_count: withData && !manual ? 2 : 0,
    latest_value: withData && !manual ? "5.0" : null,
    latest_observation_date: withData && !manual ? "2026-09-01" : null,
    source: "国家统计局",
    published_at: null,
    frequency: code === "GDP_CN" ? "Quarterly" : "Monthly",
    source_agency: "国家统计局",
    manual_only: manual,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-12345678" },
  })
}

function chinaBody(withData: boolean) {
  return { data: { series: chinaObservationCodes().map((code) => seriesItem(code, withData)), notice: "" } }
}

function observationsBody() {
  return {
    data: {
      pit_date: "2026-10-17",
      items: [
        {
          series_code: "PPI_CN",
          // 故意乱序：页面需按日期升序后再算「较上期」
          observations: [
            { observation_date: "2026-09-01", value: 5.0 },
            { observation_date: "2026-08-01", value: 4.6 },
          ],
          empty_message: null,
          truncated: false,
        },
        { series_code: "SOCIAL_FINANCING_STOCK_CN", observations: [], empty_message: "暂无数据", truncated: false },
        {
          series_code: "GDP_CN",
          observations: [
            { observation_date: "2025-07-01", value: 4.8 },
            { observation_date: "2025-10-01", value: 4.5 },
            { observation_date: "2026-01-01", value: 4.6 },
            { observation_date: "2026-04-01", value: 4.3 },
          ],
          empty_message: null,
          truncated: false,
        },
        {
          series_code: "CORE_CPI_CN",
          observations: [
            { observation_date: "2025-09-01", value: 0.9 },
            { observation_date: "2025-12-01", value: 1.2 },
            { observation_date: "2026-03-01", value: 1.1 },
            { observation_date: "2026-06-01", value: 1.0 },
          ],
          empty_message: null,
          truncated: false,
        },
      ],
    },
  }
}

const STUBS = {
  "el-date-picker": true,
  "el-button": true,
  "el-dialog": true,
  "el-select": true,
  "el-option": true,
  "el-table": true,
  "el-table-column": true,
  "router-link": true,
}

function renderPage() {
  return render(MacroChinaView, { global: { stubs: STUBS } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("中国经济页", () => {
  it("一次批量请求取回 20 个序列，并渲染 18 格滚动指标带", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/api/v1/macro/china")) {
        return Promise.resolve(jsonResponse(chinaBody(true)))
      }
      if (url.includes("/api/v1/macro/observations")) {
        return Promise.resolve(jsonResponse(observationsBody()))
      }
      return Promise.resolve(jsonResponse({ data: { observations: [] } }))
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    await waitFor(() => expect(document.querySelectorAll(".ticker-tile")).toHaveLength(18))

    const batch = fetchMock.mock.calls
      .map(([input]) => String(input))
      .filter((url) => url.includes("/api/v1/macro/observations"))
    // 指标带 18 个 + 走势图额外 2 个（CPI、M2）= 20，恰好一次批量请求
    expect(batch).toHaveLength(1)
    const requested = batch.map((url) => {
      const params = new URL(url, "http://localhost").searchParams
      expect(params.get("as_of")).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      return params.get("codes")?.split(",") ?? []
    })
    expect(requested.every((codes) => codes.length <= 20)).toBe(true)
    expect(requested.flat().sort()).toEqual([...chinaObservationCodes()].sort())
    expect(requested.flat()).toContain("CPI_CN")
    expect(document.querySelector('[aria-label="中国宏观指标"]')).not.toBeNull()
    const tiles = [...document.querySelectorAll(".ticker-tile")]
    const ppi = tiles.find((node) => node.textContent?.includes("PPI同比增速"))
    expect(ppi?.textContent).toContain("5.0")
    expect(ppi?.textContent).toContain("+0.4")
    expect(ppi?.textContent).toContain("2026-09")
  })

  it("渲染宏观象限、两张走势图与规则解读，且只用已公布数据", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/api/v1/macro/china")) {
          return Promise.resolve(jsonResponse(chinaBody(true)))
        }
        return Promise.resolve(jsonResponse(observationsBody()))
      }),
    )

    renderPage()

    expect(await screen.findByText("宏观象限：增长 × 通胀")).toBeTruthy()
    expect(screen.getByText("通胀走势")).toBeTruthy()
    expect(screen.getByText("增长与货币走势")).toBeTruthy()
    expect(screen.getByText("宏观数据对比分析")).toBeTruthy()
    expect(screen.getByText("未来宏观行情推演")).toBeTruthy()
    expect(screen.getByText("AI 宏观解读")).toBeTruthy()
    // 中国页没有可指数化的水平值序列，指数化按钮禁用
    expect((screen.getByText("指数化") as HTMLButtonElement).disabled).toBe(true)
    await waitFor(() => expect(document.body.textContent).toContain("2026 Q2：实际 GDP 当季同比 4.3%"))
    expect(document.body.textContent).toContain("共 4 个季度")
    expect(document.body.textContent).toContain("PPI 同比：2026-09 5.0%，较上期上升 +0.4 pct")
    // 社融、企业中长期贷款、政府债券等没有数据：只列出，不估算
    expect(document.body.textContent).toContain("指标带中暂无数据")
    expect(document.body.textContent).toContain("没有参与上述判断")
  })

  it("配对季度不足时象限不定位，规则解读在没有任何数据时提示不足", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/api/v1/macro/china")) {
          return Promise.resolve(jsonResponse(chinaBody(false)))
        }
        return Promise.resolve(jsonResponse({ data: { pit_date: "2026-10-17", items: [] } }))
      }),
    )

    renderPage()

    await waitFor(() => expect(document.body.textContent).toContain("配对的季度不足 4 个，象限暂不定位"))
    expect(document.body.textContent).toContain("已公布数据不足，暂不解读")
  })

  it("仅限手工导入的指标没有数据时显示「—」，其余自动来源指标照常显示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/api/v1/macro/china")) {
          return Promise.resolve(jsonResponse(chinaBody(true)))
        }
        return Promise.resolve(jsonResponse(observationsBody()))
      }),
    )

    renderPage()

    await waitFor(() => expect(document.querySelectorAll(".ticker-tile")).toHaveLength(18))
    const social = [...document.querySelectorAll(".ticker-tile")].find((node) =>
      node.textContent?.includes("社融存量同比增速"),
    )
    expect(social?.textContent).toContain("—")
  })

  it("观测接口失败时只提示指标带错误，不影响页面其余部分", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/api/v1/macro/china")) {
          return Promise.resolve(jsonResponse(chinaBody(true)))
        }
        if (url.includes("/api/v1/macro/observations")) {
          return Promise.resolve(
            jsonResponse({ error: { code: "UPSTREAM_ERROR", message: "上游暂不可用" } }, 502),
          )
        }
        return Promise.resolve(jsonResponse({ data: { observations: [] } }))
      }),
    )

    renderPage()

    expect(await screen.findByText("上游暂不可用")).toBeTruthy()
    expect(document.querySelectorAll(".ticker-tile")).toHaveLength(0)
    expect(screen.getByText("指标清单")).toBeTruthy()
  })

  it("序列接口失败时整页显示错误态", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ error: { code: "INTERNAL", message: "服务异常" } }, 500))),
    )

    renderPage()

    expect(await screen.findByText("服务异常")).toBeTruthy()
    expect(document.querySelectorAll(".ticker-tile")).toHaveLength(0)
  })
})


