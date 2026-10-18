import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue"
import { afterEach, describe, expect, it, vi } from "vitest"
import ChinaImportPanel from "../../src/views/parts/data/ChinaImportPanel.vue"

const NOW = new Date("2026-10-18T10:00:00+08:00")

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-12345678" },
  })
}

function chinaBody(latest: Record<string, string | null>) {
  return {
    data: {
      series: Object.entries(latest).map(([code, date]) => ({
        code,
        latest_observation_date: date,
        observation_count: date ? 1 : 0,
      })),
      notice: "",
    },
  }
}

const STUBS = { "router-link": true }

function renderPanel() {
  return render(ChinaImportPanel, { props: { now: NOW }, global: { stubs: STUBS } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("中国宏观人民银行月度导入面板", () => {
  it("数据落后时提醒导入，列出待导入指标，并给出人民银行官方链接", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            chinaBody({
              SOCIAL_FINANCING_STOCK_CN: "2026-09-01",
              GOV_BOND_NET_CN: "2026-08-01",
              ENT_MLT_LOAN_CN: null,
            }),
          ),
        ),
      ),
    )

    renderPanel()

    const remind = await screen.findByTestId("china-import-remind")
    await waitFor(() => expect(remind.textContent).toContain("请导入"))
    expect(remind.textContent).toContain("政府债券净融资")
    expect(remind.textContent).toContain("企业中长期贷款增量")
    expect(remind.textContent).not.toContain("社融存量同比增速 还没有")
    expect(remind.textContent).toContain("2026-09")
    expect(remind.textContent).toContain("12–15 日")
    expect(document.body.textContent).toContain("待导入：最新 2026-08，应有 2026-09")
    expect(document.body.textContent).toContain("暂无数据，待导入")
    expect(document.body.textContent).toContain("已是最新（2026-09）")

    const links = [...document.querySelectorAll("a")].filter((node) =>
      node.getAttribute("href")?.startsWith("https://www.pbc.gov.cn/"),
    )
    expect(links.length).toBeGreaterThanOrEqual(2)
    for (const link of links) {
      expect(link.getAttribute("target")).toBe("_blank")
      expect(link.getAttribute("rel")).toContain("noopener")
    }
  })

  it("三项都是最新时不再催促导入", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            chinaBody({
              SOCIAL_FINANCING_STOCK_CN: "2026-09-01",
              GOV_BOND_NET_CN: "2026-09-01",
              ENT_MLT_LOAN_CN: "2026-09-01",
            }),
          ),
        ),
      ),
    )

    renderPanel()

    await waitFor(() =>
      expect(screen.getByTestId("china-import-remind").textContent).toContain("三项都已是最新"),
    )
    expect(screen.getByTestId("china-import-remind").textContent).not.toContain("请导入")
  })

  it("读取状态失败时仍显示提醒和官方链接，不假装数据是最新", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({ error: { code: "INTERNAL", message: "服务暂不可用" }, request_id: "r1" }, 500),
        ),
      ),
    )

    renderPanel()

    await waitFor(() =>
      expect(screen.getByTestId("china-import-remind").textContent).toContain("服务暂不可用"),
    )
    expect(screen.getByTestId("china-import-remind").textContent).toContain("12–15 日")
    expect(document.body.textContent).not.toContain("已是最新")
    expect(document.querySelectorAll('a[href^="https://www.pbc.gov.cn/"]').length).toBeGreaterThanOrEqual(2)
  })

  it("选择 CSV 后导入：发送 csv_text，显示入库条数并刷新状态", async () => {
    let imported = false
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.includes("/api/v1/macro/china/observations")) {
        imported = true
        return Promise.resolve(
          jsonResponse({ data: { accepted: 3, duplicate_count: 1, errors: ["第 4 行日期或数值无法解析"], series: [] } }),
        )
      }
      return Promise.resolve(
        jsonResponse(
          chinaBody({
            SOCIAL_FINANCING_STOCK_CN: imported ? "2026-09-01" : "2026-07-01",
            GOV_BOND_NET_CN: "2026-09-01",
            ENT_MLT_LOAN_CN: "2026-09-01",
          }),
        ),
      )
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPanel()
    await waitFor(() =>
      expect(screen.getByTestId("china-import-remind").textContent).toContain("请导入"),
    )

    const csv = "series_code,observation_date,value,unit,source,published_at\n"
    const input = screen.getByLabelText("选择 CSV 文件")
    await fireEvent.change(input, { target: { files: [new File([csv], "pboc.csv", { type: "text/csv" })] } })
    await fireEvent.click(screen.getByText("导入"))

    await waitFor(() => expect(screen.getByTestId("china-import-result").textContent).toContain("入库 3 条"))
    expect(screen.getByTestId("china-import-result").textContent).toContain("重复 1 条")
    expect(document.body.textContent).toContain("第 4 行日期或数值无法解析")

    const post = fetchMock.mock.calls.find(([input]) =>
      String(input).includes("/api/v1/macro/china/observations"),
    )
    expect(post?.[1]?.method).toBe("POST")
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ csv_text: csv })

    await waitFor(() =>
      expect(screen.getByTestId("china-import-remind").textContent).toContain("三项都已是最新"),
    )
  })

  it("没选文件、空文件或接口拒绝时给出明确提示，不改变状态", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).includes("/api/v1/macro/china/observations")) {
        return Promise.resolve(
          jsonResponse({ error: { code: "FORBIDDEN", message: "需要管理员权限" }, request_id: "r2" }, 403),
        )
      }
      return Promise.resolve(
        jsonResponse(chinaBody({ SOCIAL_FINANCING_STOCK_CN: null, GOV_BOND_NET_CN: null, ENT_MLT_LOAN_CN: null })),
      )
    })
    vi.stubGlobal("fetch", fetchMock)

    renderPanel()
    await screen.findByTestId("china-import-remind")

    await fireEvent.click(screen.getByText("导入"))
    expect(screen.getByTestId("china-import-result").textContent).toContain("请先选择 CSV 文件")

    const input = screen.getByLabelText("选择 CSV 文件")
    await fireEvent.change(input, { target: { files: [new File(["  "], "empty.csv")] } })
    await fireEvent.click(screen.getByText("导入"))
    await waitFor(() => expect(screen.getByTestId("china-import-result").textContent).toContain("文件是空的"))

    await fireEvent.change(input, { target: { files: [new File(["a,b\n1,2\n"], "x.csv")] } })
    await fireEvent.click(screen.getByText("导入"))
    await waitFor(() => expect(screen.getByTestId("china-import-result").textContent).toContain("需要管理员权限"))
    expect(screen.getByTestId("china-import-remind").textContent).toContain("请导入")
  })
})
