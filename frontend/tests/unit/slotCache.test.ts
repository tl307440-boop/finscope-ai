import { beforeEach, describe, expect, it, vi } from "vitest"
import { FetchFailure } from "../../src/api/industryMarket"
import { forgetAll, isTransient, recall, remember, withRetry } from "../../src/api/slotCache"

const instantly = () => Promise.resolve()

describe("slotCache", () => {
  beforeEach(() => forgetAll())

  it("recalls what was remembered until it is too old", () => {
    remember("market", { score: 50 }, 1_000)
    expect(recall("market", 1_000 + 60_000)).toEqual({ score: 50 })
    expect(recall("market", 1_000 + 11 * 60_000)).toBeNull()
    expect(recall("market", 1_000 + 12 * 60_000)).toBeNull()
  })

  it("returns null for unknown keys", () => {
    expect(recall("missing")).toBeNull()
  })

  it("classifies transient failures", () => {
    expect(isTransient(new FetchFailure("网络连接失败", 0))).toBe(true)
    expect(isTransient(new FetchFailure("上游超时", 504))).toBe(true)
    expect(isTransient(new FetchFailure("上游限流", 429))).toBe(true)
    expect(isTransient(new FetchFailure("没有权限", 403))).toBe(false)
    expect(isTransient(new FetchFailure("参数错误", 422))).toBe(false)
    expect(isTransient(new Error("解析失败"))).toBe(false)
  })

  it("retries a transient failure exactly once", async () => {
    const loader = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new FetchFailure("上游连接中断", 502))
      .mockResolvedValueOnce("ok")
    await expect(withRetry(loader, instantly)).resolves.toBe("ok")
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it("gives up after the single retry", async () => {
    const loader = vi.fn<() => Promise<string>>().mockRejectedValue(new FetchFailure("上游超时", 504))
    await expect(withRetry(loader, instantly)).rejects.toThrow("上游超时")
    expect(loader).toHaveBeenCalledTimes(2)
  })

  it("does not retry client errors", async () => {
    const loader = vi.fn<() => Promise<string>>().mockRejectedValue(new FetchFailure("没有权限", 403))
    await expect(withRetry(loader, instantly)).rejects.toThrow("没有权限")
    expect(loader).toHaveBeenCalledTimes(1)
  })
})
