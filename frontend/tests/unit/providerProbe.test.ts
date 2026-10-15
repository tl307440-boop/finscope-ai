import { describe, expect, it } from "vitest"
import { describeProbeFailure } from "../../src/api/providerProbe"

describe("上游探测结果", () => {
  it("限流、不可用和超时都说明旧数据还在", () => {
    for (const message of ["上游限流", "上游暂时不可用", "上游超时"]) {
      const text = describeProbeFailure({
        error: {
          code: "UPSTREAM_TIMEOUT",
          message,
          details: { rows_written: 0, preserved_existing: true, attempts: 3 },
        },
        request_id: "req-12345678",
      })
      expect(text).toBe(`${message}。已有数据未改动。`)
    }
  })

  it("没有保留标记时不假装数据安全", () => {
    expect(describeProbeFailure({ error: { message: "上游请求失败", details: {} } })).toBe(
      "上游请求失败",
    )
  })
})
