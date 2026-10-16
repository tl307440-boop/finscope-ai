import { describe, expect, it } from "vitest"
import { parseDataStatus, parseFreshness, stateTone } from "../../src/api/companyData"

describe("数据就绪度解析", () => {
  it("保留后端的状态、同步进度与不可用原因", () => {
    const parsed = parseDataStatus({
      data: {
        instrument_id: "abc",
        generated_at: "2026-10-16T09:00:00Z",
        syncing: true,
        datasets: [
          {
            dataset: "financials",
            label: "财务报表",
            source: "SEC EDGAR",
            state: "missing",
            sync: "running",
            message: "后台正在抓取",
            can_request: false,
          },
          {
            dataset: "bars",
            label: "日线行情",
            source: "Tiingo EOD",
            state: "unavailable",
            sync: "idle",
            latest_data_date: null,
            message: "未配置美股行情源密钥",
            can_request: false,
          },
        ],
      },
    })
    expect(parsed?.syncing).toBe(true)
    expect(parsed?.datasets.map((row) => [row.dataset, row.state, row.sync])).toEqual([
      ["financials", "missing", "running"],
      ["bars", "unavailable", "idle"],
    ])
    expect(parsed?.datasets[1]?.message).toBe("未配置美股行情源密钥")
    expect(parsed?.datasets[1]?.latestDataDate).toBeNull()
  })

  it("未知状态按缺失处理，不当作最新", () => {
    const parsed = parseDataStatus({
      data: { datasets: [{ dataset: "bars", state: "weird", sync: "weird" }] },
    })
    expect(parsed?.datasets[0]?.state).toBe("missing")
    expect(parsed?.datasets[0]?.sync).toBe("idle")
  })

  it("响应没有 data 时返回 null", () => {
    expect(parseDataStatus({})).toBeNull()
    expect(parseFreshness(null)).toBeNull()
  })

  it("新鲜度报告带上调度器心跳与分组", () => {
    const parsed = parseFreshness({
      data: {
        generated_at: "2026-10-16T09:00:00Z",
        scheduler_running: true,
        items: [{ group: "macro", key: "CPIAUCSL", label: "CPI", state: "stale", detail: "月频" }],
      },
    })
    expect(parsed?.schedulerRunning).toBe(true)
    expect(parsed?.items[0]).toMatchObject({ group: "macro", state: "stale" })
    expect(stateTone("stale")).toBe("danger")
    expect(stateTone("fresh")).toBe("ok")
  })
})
