import { describe, expect, it } from "vitest"
import { summarizeSearch } from "../../src/api/instrumentSearch"

describe("证券搜索摘要", () => {
  it("唯一结果展示交易所代码", () => {
    expect(
      summarizeSearch({
        unique: true,
        empty_message: null,
        results: [
          {
            canonical_code: "SSE:600519",
            name_cn: "贵州茅台",
            instrument_type: "STOCK",
            display_symbol: "600519.SH",
          },
        ],
      }),
    ).toEqual(["唯一交易所代码 SSE:600519", "贵州茅台 · 证券 · 600519.SH"])
  })

  it("没有结果时直接说明", () => {
    expect(summarizeSearch({ unique: false, empty_message: "暂无该证券", results: [] })).toEqual([
      "暂无该证券",
    ])
  })

  it("重名代码不合并成一个交易所", () => {
    const lines = summarizeSearch({
      unique: false,
      empty_message: null,
      results: [
        {
          canonical_code: "SSE:000001",
          name_cn: "上证指数",
          instrument_type: "INDEX",
          display_symbol: "000001.SH",
        },
        {
          canonical_code: "SZSE:000001",
          name_cn: "平安银行",
          instrument_type: "STOCK",
          display_symbol: "000001.SZ",
        },
      ],
    })
    expect(lines[0]).toContain("多条")
    expect(lines).toContain("SSE:000001 上证指数")
    expect(lines).toContain("SZSE:000001 平安银行")
  })
})
