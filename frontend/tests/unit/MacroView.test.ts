import { cleanup, render, screen, waitFor } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { afterEach, describe, expect, it, vi } from "vitest"

// ECharts 依赖 canvas，happy-dom 下只验证页面取数与指标带，不画图。
vi.mock("../../src/charts", () => ({
  VChart: defineComponent({ name: "VChart", render: () => h("div", { class: "chart-stub" }) }),
}))

import MacroView from "../../src/views/MacroView.vue"
import { tileCodes } from "../../src/api/macroTiles"

const ISM_SOURCE_KEYS: Record<string, string> = { ISM_SVC: "EMG00002790", ISM_MFG: "EMG00002791" }

function catalogEntry(code: string, frequency: string, unit = "Percent") {
  const ism = code in ISM_SOURCE_KEYS
  return {
    series_code: code,
    name_cn: code,
    name_en: code,
    category: "macro",
    frequency,
    base_unit: unit,
    seasonal_adjustment: "SA",
    provider: ism ? "eastmoney" : "fred",
    source_key: ism ? ISM_SOURCE_KEYS[code] : code,
  }
}

/** 走势图用到的序列（与 MacroView 的 PLOT_SPECS 一致）。 */
const PLOT_CODES = [
  "CPIAUCSL",
  "PCEPILFE",
  "UNRATE",
  "CES0500000003",
  "GDPC1",
  "PCEC96",
  "DFEDTARU",
  "DGS5",
  "DGS10",
  "DGS20",
  "T10Y2Y",
]

const CATALOG = [
  catalogEntry("CPIAUCSL", "Monthly", "Index"),
  catalogEntry("UNRATE", "Monthly"),
  catalogEntry("DGS10", "Daily"),
  catalogEntry("GDP", "Quarterly", "Billions of Dollars"),
]

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-12345678" },
  })
}

function catalogBody() {
  return { data: { provider_code: "fred", availability: "configured", notice: "", series: CATALOG } }
}

function observationsBody() {
  return {
    data: {
      pit_date: "2026-10-17",
      items: [
        {
          series_code: "UNRATE",
          // 故意乱序：页面需按日期升序后再算「较上期」
          observations: [
            { observation_date: "2026-09-01", value: 4.3 },
            { observation_date: "2026-08-01", value: 4.2 },
          ],
          empty_message: null,
          truncated: false,
        },
        { series_code: "DGS10", observations: [], empty_message: "暂无数据", truncated: false },
      ],
    },
  }
}

const assessmentBody = {
  data: {
    pit_date: "2026-10-17",
    regime_code: "insufficient_data",
    growth: null,
    inflation: null,
    liquidity: null,
    notice: "",
    version: "v1",
  },
}

const STUBS = { "el-date-picker": true, "el-button": true, "el-dialog": true, "router-link": true }

function renderPage() {
  return render(MacroView, { global: { stubs: STUBS } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("美国经济页", () => {
  it("一次批量请求取回目录内序列，并渲染 15 格指标带", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/api/v1/macro/series")) {
        return Promise.resolve(jsonResponse(catalogBody()))
      }
      if (url.includes("/api/v1/macro/observations")) {
        return Promise.resolve(jsonResponse(observationsBody()))
      }
      return Promise.resolve(jsonResponse(assessmentBody))
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    await waitFor(() => expect(document.querySelectorAll(".ticker-tile")).toHaveLength(15))

    const urls = fetchMock.mock.calls.map(([input]) => String(input))
    expect(urls.some((url) => url.includes("/api/v1/macro/series/"))).toBe(false)
    const batch = urls.filter((url) => url.includes("/api/v1/macro/observations"))
    expect(batch).toHaveLength(1)
    const params = new URL(batch[0] ?? "", "http://localhost").searchParams
    // 只请求目录里有的序列；GDP 已离开指标带与走势图，不再请求
    expect(params.get("codes")?.split(",").sort()).toEqual(["CPIAUCSL", "DGS10", "UNRATE"])
    expect(params.get("as_of")).toMatch(/^\d{4}-\d{2}-\d{2}$/)

    const tiles = [...document.querySelectorAll(".ticker-tile")]
    const unrate = tiles.find((node) => node.textContent?.includes("失业率"))
    expect(unrate?.textContent).toContain("4.30")
    expect(unrate?.textContent).toContain("+0.10 pct")
    expect(unrate?.textContent).toContain("2026-09")
  })

  it("目录齐全时一次请求指标带 ∪ 走势图的全部序列，且不超过批量上限 20", async () => {
    const expected = [...new Set([...tileCodes(), ...PLOT_CODES])].sort()
    // GDP / FEDFUNDS / DGS2 仍在目录里，但不属于指标带与走势图，不应请求
    const catalog = [...expected, "GDP", "FEDFUNDS", "DGS2"].map((code) => catalogEntry(code, "Monthly"))
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes("/api/v1/macro/series")) {
        return Promise.resolve(
          jsonResponse({ data: { provider_code: "fred", availability: "configured", notice: "", series: catalog } }),
        )
      }
      if (url.includes("/api/v1/macro/observations")) {
        return Promise.resolve(jsonResponse({ data: { pit_date: "2026-10-17", items: [] } }))
      }
      return Promise.resolve(jsonResponse(assessmentBody))
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPage()

    await waitFor(() => expect(document.querySelectorAll(".ticker-tile")).toHaveLength(15))
    const batch = fetchMock.mock.calls.map(([input]) => String(input)).filter((url) => url.includes("/macro/observations"))
    expect(batch).toHaveLength(1)
    const codes = new URL(batch[0] ?? "", "http://localhost").searchParams.get("codes")?.split(",") ?? []
    expect(codes).toHaveLength(new Set(codes).size)
    expect([...codes].sort()).toEqual(expected)
    expect(codes.length).toBeLessThanOrEqual(20)
    expect(codes).toContain("DFEDTARL")

    const ism = [...document.querySelectorAll(".ticker-tile")].find((node) =>
      node.textContent?.includes("ISM 服务业 PMI"),
    )
    expect(ism?.textContent).toContain("东方财富指标 EMG00002790")
  })

  it("批量接口出错时整页如实显示错误，不显示指标", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/api/v1/macro/series")) {
          return Promise.resolve(jsonResponse(catalogBody()))
        }
        if (url.includes("/api/v1/macro/observations")) {
          return Promise.resolve(
            jsonResponse(
              {
                error: { code: "VALIDATION_ERROR", message: "存在未知序列代码", details: { unknown_codes: ["X"] } },
                request_id: "req-12345678",
              },
              422,
            ),
          )
        }
        return Promise.resolve(jsonResponse(assessmentBody))
      }),
    )

    renderPage()

    expect(await screen.findByText(/存在未知序列代码/)).toBeTruthy()
    expect(document.querySelectorAll(".ticker-tile")).toHaveLength(0)
  })
})
