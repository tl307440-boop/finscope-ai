import { cleanup, fireEvent, render, screen } from "@testing-library/vue"
import { afterEach, describe, expect, it, vi } from "vitest"
import SystemStatusView from "../../src/views/SystemStatusView.vue"

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

const ready = {
  data: {
    status: "ready",
    checks: {
      database: { status: "up", latency_ms: 4 },
      worker: { status: "up", last_seen_at: "2026-10-15T02:00:00Z" },
    },
  },
  meta: live.meta,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "X-Request-ID": "req-12345678",
    },
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("运行状态页", () => {
  it("先显示加载，再显示就绪和业务空状态", async () => {
    let resolveReady: (response: Response) => void = () => undefined
    const readyPromise = new Promise<Response>((resolve) => {
      resolveReady = resolve
    })
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/health/live")) {
          return Promise.resolve(jsonResponse(live))
        }
        return readyPromise
      }),
    )

    render(SystemStatusView)
    expect(screen.getByText("正在检查运行状态")).toBeTruthy()
    resolveReady(jsonResponse(ready))
    expect(await screen.findByRole("heading", { name: "运行正常" })).toBeTruthy()
    // 业务覆盖只做定性说明（后端没有覆盖率汇总接口），这里核对它确实写明了边界。
    expect(screen.getByText("未配置数据源密钥时不填观测值，页面显示「不可用」而不是旧值。")).toBeTruthy()
    expect(screen.getByText("数据模式：live，未使用演示数据")).toBeTruthy()
    expect(screen.getByText("延迟 4 毫秒（就绪检查实测）")).toBeTruthy()
  })

  it("Worker 没有心跳时展示缺失，不显示运行正常", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/health/live")) {
          return Promise.resolve(jsonResponse(live))
        }
        return Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "SERVICE_UNAVAILABLE",
                message: "服务尚未就绪",
                details: {
                  checks: {
                    database: { status: "up", latency_ms: 2 },
                    worker: { status: "down", reason: "heartbeat_missing" },
                  },
                },
              },
              request_id: "req-12345678",
            },
            503,
          ),
        )
      }),
    )

    render(SystemStatusView)
    expect(await screen.findByRole("heading", { name: "部分组件不可用" })).toBeTruthy()
    expect(screen.getByText("尚未上报心跳")).toBeTruthy()
    expect(screen.queryByRole("heading", { name: "运行正常" })).toBeNull()
  })

  it("网络失败时提供重试，重试成功后进入就绪", async () => {
    let online = false
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (!online) {
          return Promise.reject(new Error("offline"))
        }
        const url = String(input)
        if (url.endsWith("/health/live")) {
          return Promise.resolve(jsonResponse(live))
        }
        return Promise.resolve(jsonResponse(ready))
      }),
    )

    render(SystemStatusView)
    const retry = await screen.findByRole("button", { name: "重试" })
    expect(screen.getByText("无法连接接口")).toBeTruthy()
    online = true
    await fireEvent.click(retry)
    expect(await screen.findByRole("heading", { name: "运行正常" })).toBeTruthy()
  })

  it("演示模式显示醒目的非实时标记", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith("/health/live")) {
          return Promise.resolve(
            jsonResponse({
              ...live,
              meta: { ...live.meta, data_mode: "demo", notice: "演示数据/非实时" },
            }),
          )
        }
        return Promise.resolve(jsonResponse(ready))
      }),
    )

    render(SystemStatusView)
    expect(await screen.findByText("演示数据 / 非实时")).toBeTruthy()
  })
})
