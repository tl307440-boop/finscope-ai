import { describe, expect, it } from "vitest"
import { fetchRegimeHistory, parseRegimeHistory } from "../../src/api/regimeHistory"

describe("regime history", () => {
  it("parses snapshots and keeps missing scores as null", () => {
    const history = parseRegimeHistory({
      data: {
        notice: "这是规则计算，不是经济定论。",
        items: [
          {
            as_of: "2026-10-16",
            regime_code: "up-hot-tight",
            growth: 70,
            inflation: null,
            liquidity: 30,
            rule_version: "macro-rules-v1",
            computed_at: "2026-10-16T15:00:00Z",
          },
          { as_of: "", regime_code: "x" },
        ],
      },
    })
    expect(history.items).toHaveLength(1)
    expect(history.items[0]?.inflation).toBeNull()
    expect(history.items[0]?.growth).toBe(70)
    expect(history.notice).toContain("规则计算")
  })

  it("surfaces backend errors instead of returning an empty list", async () => {
    const failing = (async () =>
      new Response(JSON.stringify({ error: { code: "INTERNAL", message: "出错了" } }), {
        status: 500,
      })) as unknown as typeof fetch
    await expect(fetchRegimeHistory(5, failing)).rejects.toThrow("出错了")
  })
})
