import { afterEach, describe, expect, it, vi } from "vitest"
import { fetchMarket } from "../../src/api/dashboard"
import { isTimeout, timeoutSignal } from "../../src/api/client"
import { FetchFailure } from "../../src/api/industryMarket"
import { isTransient, withRetry } from "../../src/api/slotCache"

afterEach(() => vi.unstubAllGlobals())

describe("request timeout", () => {
  it("builds an abort signal that fires after the given time", async () => {
    const signal = timeoutSignal(10)
    expect(signal).toBeDefined()
    await new Promise((resolve) => setTimeout(resolve, 40))
    expect(signal?.aborted).toBe(true)
  })

  it("recognises timeout errors only", () => {
    expect(isTimeout(new DOMException("timed out", "TimeoutError"))).toBe(true)
    expect(isTimeout(new DOMException("aborted", "AbortError"))).toBe(false)
    expect(isTimeout(new TypeError("Failed to fetch"))).toBe(false)
    expect(isTimeout(null)).toBe(false)
  })

  it("passes a signal to dashboard requests and reports timeouts as a retryable failure", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new DOMException("timed out", "TimeoutError"))
    vi.stubGlobal("fetch", fetchMock)

    const failure = await fetchMarket().catch((error: unknown) => error)

    expect(failure).toBeInstanceOf(FetchFailure)
    expect((failure as FetchFailure).message).toBe("请求超时，请稍后重试")
    expect(isTransient(failure)).toBe(true)
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(init.signal).toBeDefined()
  })

  it("retries a timed-out request with a fresh signal and a shorter deadline", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("timed out", "TimeoutError"))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { trade_date: "2026-10-20" } }), { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    const timeouts: number[] = []
    const spy = vi.spyOn(AbortSignal, "timeout")

    const market = await withRetry((ms) => {
      timeouts.push(ms)
      return fetchMarket(ms)
    }, () => Promise.resolve())

    expect(market.tradeDate).toBe("2026-10-20")
    expect(timeouts).toEqual([30_000, 15_000])
    expect(spy.mock.calls.map(([ms]) => ms)).toEqual([30_000, 15_000])
    const signals = fetchMock.mock.calls.map((call) => (call[1] as RequestInit).signal)
    expect(signals[0]).toBeDefined()
    expect(signals[1]).toBeDefined()
    expect(signals[1]).not.toBe(signals[0])
    expect(signals[1]?.aborted).toBe(false)
    spy.mockRestore()
  })
})
