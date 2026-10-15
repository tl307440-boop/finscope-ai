// @vitest-environment node
import { describe, expect, it } from "vitest"
import { ApiError } from "../../src/api/client"
import { formatShanghai, parseSystemStatus, reasonText } from "../../src/api/health"

const live = {
  data: { status: "alive", service: "api" },
  meta: {
    request_id: "req-12345678",
    as_of: "2026-10-15T02:00:00Z",
    source: "internal",
    stale: false,
    data_mode: "live",
  },
}

describe("运行状态解析", () => {
  it("接受数据库和 Worker 都就绪的响应", () => {
    const snapshot = parseSystemStatus(live, 200, {
      data: {
        status: "ready",
        checks: {
          database: { status: "up", latency_ms: 3 },
          worker: { status: "up", last_seen_at: "2026-10-15T02:00:00Z" },
        },
      },
      meta: live.meta,
    })
    expect(snapshot.ready).toBe(true)
    expect(snapshot.database.latencyMs).toBe(3)
    expect(snapshot.dataMode).toBe("live")
  })

  it("把缺失心跳显示为可解释的空状态", () => {
    const snapshot = parseSystemStatus(live, 503, {
      error: {
        code: "SERVICE_UNAVAILABLE",
        message: "服务尚未就绪",
        details: {
          checks: {
            database: { status: "up" },
            worker: { status: "down", reason: "heartbeat_missing" },
          },
        },
      },
      request_id: "req-12345678",
    })
    expect(snapshot.ready).toBe(false)
    expect(reasonText(snapshot.worker.reason)).toBe("尚未上报心跳")
  })

  it("把过期心跳标成过期而不是正常", () => {
    const snapshot = parseSystemStatus(live, 503, {
      error: {
        code: "SERVICE_UNAVAILABLE",
        message: "服务尚未就绪",
        details: {
          checks: {
            database: { status: "down", reason: "database_unavailable" },
            worker: { status: "down", reason: "heartbeat_stale" },
          },
        },
      },
      request_id: "req-12345678",
    })
    expect(reasonText(snapshot.worker.reason)).toBe("心跳过期")
    expect(reasonText(snapshot.database.reason)).toBe("数据库不可用")
  })

  it("保留演示模式标记", () => {
    const snapshot = parseSystemStatus(
      {
        ...live,
        meta: { ...live.meta, data_mode: "demo", notice: "演示数据/非实时" },
      },
      200,
      {
        data: {
          status: "ready",
          checks: {
            database: { status: "up" },
            worker: { status: "up" },
          },
        },
      },
    )
    expect(snapshot.dataMode).toBe("demo")
    expect(snapshot.notice).toContain("演示数据")
  })

  it("拒绝缺少检查项的响应", () => {
    expect(() => parseSystemStatus(live, 500, { error: { message: "坏了" } })).toThrow(ApiError)
  })

  it("按北京时间显示 UTC 时刻", () => {
    expect(formatShanghai("2026-10-15T02:00:00Z")).toContain("10:00:00")
    expect(formatShanghai(undefined)).toBe("—")
  })
})
