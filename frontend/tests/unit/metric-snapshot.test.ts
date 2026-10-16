import { describe, expect, it } from "vitest"
import {
  fetchMetricSnapshot,
  formatMetric,
  parseMetricSnapshot,
} from "../../src/api/metricSnapshot"

const body = {
  data: {
    notice: "指标由后端计算",
    items: [
      {
        metric_code: "gross_margin",
        period_type: "FY",
        period_end: "2025-12-31",
        value: "40.000000",
        unit: "%",
        reason: null,
        inputs: [{ source_url: "https://example.test/a" }, { source_url: "https://example.test/a" }],
      },
      {
        metric_code: "revenue_yoy",
        period_type: "FY",
        period_end: "2025-12-31",
        value: null,
        unit: "%",
        reason: "缺少去年同期同口径事实",
        inputs: [],
      },
    ],
  },
}

describe("metric snapshot", () => {
  it("orders items, dedupes sources and keeps the reason for missing values", () => {
    const snapshot = parseMetricSnapshot(body)
    expect(snapshot.status).toBe("ready")
    expect(snapshot.items.map((item) => item.code)).toEqual(["revenue_yoy", "gross_margin"])
    expect(snapshot.items[1]?.sources).toEqual(["https://example.test/a"])
    expect(snapshot.items[0]?.reason).toContain("去年同期")
    expect(formatMetric(snapshot.items[0]!)).toBe("—")
    expect(formatMetric(snapshot.items[1]!)).toBe("40.00%")
  })

  it("reports empty when the backend has no snapshot", () => {
    expect(parseMetricSnapshot({ data: { items: [], notice: "还没有" } }).status).toBe("empty")
  })

  it("throws backend errors", async () => {
    const failing = (async () =>
      new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "查无该证券" } }), {
        status: 404,
      })) as unknown as typeof fetch
    await expect(fetchMetricSnapshot("x", failing)).rejects.toThrow("查无该证券")
  })
})
