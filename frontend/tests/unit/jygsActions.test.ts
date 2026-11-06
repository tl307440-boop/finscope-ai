import { afterEach, describe, expect, it, vi } from "vitest"

import { fetchJygsActions, fetchJygsStatus } from "../../src/api/aSharePanorama"
import { pollJygsLogin, startJygsLogin } from "../../src/api/jygsLogin"

function mockFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fn = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fn)
  return fn
}

const META = { tradeDate: "2026-11-06", session: "intraday", sources: [], notes: [], unavailable: {} }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("fetchJygsActions", () => {
  it("把板块与个股解析成前端结构，缺失字段为 null / 空串而不是 0", async () => {
    const fn = mockFetch({
      success: true,
      data: {
        tradeDate: "2026-11-06",
        totalStocks: 2,
        sectors: [
          {
            name: "光伏",
            reason: "板块催化",
            stocks: [
              {
                code: "002578.SZ",
                name: "闽发铝业",
                boardLabel: "10天6板",
                price: 5.01,
                changePct: 10.11,
                firstSealTime: "09:30:09",
                explain: "解析正文",
              },
              { code: "002529.SZ", name: "海源复材" },
            ],
          },
        ],
        meta: META,
      },
    })
    const data = await fetchJygsActions("2026-11-06")
    expect(String(fn.mock.calls[0]?.[0])).toContain("/api/v1/ashare/jygs-actions?date=2026-11-06")
    expect(data.totalStocks).toBe(2)
    const [first, second] = data.sectors[0]!.stocks
    expect(first).toMatchObject({ code: "002578.SZ", price: 5.01, changePct: 10.11 })
    expect(second).toMatchObject({ price: null, changePct: null, boardLabel: "", explain: "" })
  })

  it("没配置登录态时返回空板块并保留后端说明", async () => {
    mockFetch({
      success: true,
      data: {
        tradeDate: "2026-11-06",
        totalStocks: 0,
        sectors: [],
        meta: { ...META, notes: ["未配置 JYGS_SESSION，未接入韭研公社异动解析"] },
      },
    })
    const data = await fetchJygsActions()
    expect(data.sectors).toEqual([])
    expect(data.meta.notes[0]).toContain("JYGS_SESSION")
  })
})

describe("fetchJygsStatus", () => {
  it("解析会话状态；不认识的状态当成 unknown，不当成已登录", async () => {
    mockFetch({
      success: true,
      data: { state: "expiring", message: "还剩 2 天", expiresAt: "2026-11-08T00:00:00+00:00", daysLeft: 2 },
    })
    expect(await fetchJygsStatus()).toMatchObject({ state: "expiring", daysLeft: 2 })
    mockFetch({ success: true, data: { state: "weird", message: "?" } })
    expect(await fetchJygsStatus()).toMatchObject({ state: "unknown", daysLeft: null })
  })
})

describe("jygs login api", () => {
  it("取二维码与扫码进度；未知状态按失败处理", async () => {
    const fn = mockFetch({
      success: true,
      data: { loginId: "a".repeat(32), qrImage: "data:image/jpeg;base64,AAAA", expiresInSeconds: 150 },
    })
    const started = await startJygsLogin("tok", async () => null)
    expect(started.qrImage.startsWith("data:image/jpeg")).toBe(true)
    expect(String(fn.mock.calls[0]?.[0])).toContain("/api/v1/admin/jygs-login")

    mockFetch({ success: true, data: { status: "scanned", message: "已扫码" } })
    expect(await pollJygsLogin("x", "tok", async () => null)).toEqual({
      status: "scanned",
      message: "已扫码",
    })
    mockFetch({ success: true, data: { status: "???", message: "" } })
    expect((await pollJygsLogin("x", "tok", async () => null)).status).toBe("failed")
  })
})
