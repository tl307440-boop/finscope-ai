import { describe, expect, it } from "vitest"
import {
  parsePcr,
  parseSources,
  parseValuation,
  pcrCsvUrl,
  type Indicator,
  type PcrPoint,
} from "../../src/api/customAnalysis"
import {
  VISIBLE_DAYS,
  buildIndicatorOption,
  buildIndicatorPercentileOption,
  buildPcrOption,
  buildPcrPercentileOption,
  hasPercentiles,
  zoomStart,
} from "../../src/views/parts/custom/customCharts"

function pcrBody() {
  return {
    data: {
      group_code: "csi300",
      group_name: "沪深300",
      index_code: "000300",
      index_name: "沪深300指数",
      rules: ["PCR = 认沽量 / 认购量"],
      rules_version: "custom-analysis-v1",
      baskets: [
        {
          kind: "etf",
          label: "ETF 期权",
          members: ["SSE 510300", "SZSE 159919"],
          note: "两只 ETF 加总",
          status: "ok",
          first_date: "2025-10-16",
          last_date: "2026-10-16",
          history_years: 1,
          incomplete_days: 2,
          latest: {
            trade_date: "2026-10-16",
            call_volume: 1500,
            put_volume: 1049,
            call_oi: 3000,
            put_oi: 2800,
            volume_pcr: 0.6993,
            sentiment_signal: "正常",
            quality_flag: "ok",
            // index_close、oi_pcr 等 null 字段会被后端省略
          },
          points: [
            { trade_date: "2026-10-15", call_volume: 1, put_volume: 1, call_oi: 1, put_oi: 1, quality_flag: "ok" },
            { trade_date: "2026-10-16", call_volume: 1500, put_volume: 1049, call_oi: 3000, put_oi: 2800, volume_pcr: 0.6993, index_close: 4600, quality_flag: "review" },
            { call_volume: 1 }, // 没有日期的行必须丢弃
          ],
        },
        { kind: "bogus", label: "不认识的类型" },
        {
          kind: "index",
          label: "股指期权",
          members: ["CFFEX IO"],
          note: "",
          status: "unavailable",
          reason: "中金所当天没有文件",
        },
      ],
    },
  }
}

describe("PCR 响应解析", () => {
  it("缺失字段保持 null，不补 0；无日期的行与未知篮子被丢弃", () => {
    const view = parsePcr(pcrBody())
    expect(view?.groupName).toBe("沪深300")
    expect(view?.baskets.map((basket) => basket.kind)).toEqual(["etf", "index"])
    const etf = view?.baskets[0]
    expect(etf?.points).toHaveLength(2)
    expect(etf?.points[0]?.volumePcr).toBeNull()
    expect(etf?.points[0]?.indexClose).toBeNull()
    expect(etf?.latest?.oiPcr).toBeNull()
    expect(etf?.latest?.percentile5y).toBeNull()
    expect(etf?.latest?.volumePcr).toBe(0.6993)
    expect(etf?.incompleteDays).toBe(2)
    expect(etf?.points[1]?.qualityFlag).toBe("review")
  })

  it("不可用的篮子带着后端给的原因，没有 latest 与 points", () => {
    const index = parsePcr(pcrBody())?.baskets[1]
    expect(index?.status).toBe("unavailable")
    expect(index?.reason).toBe("中金所当天没有文件")
    expect(index?.latest).toBeNull()
    expect(index?.points).toEqual([])
  })

  it("格式不对的响应返回 null，而不是抛错或造空数据", () => {
    expect(parsePcr(null)).toBeNull()
    expect(parsePcr({ data: {} })).toBeNull()
    expect(parsePcr({ data: { group_code: "csi300", baskets: "oops" } })?.baskets).toEqual([])
  })

  it("未知状态一律当作 unavailable", () => {
    const body = pcrBody()
    body.data.baskets[0]!.status = "weird"
    expect(parsePcr(body)?.baskets[0]?.status).toBe("unavailable")
  })

  it("CSV 链接带上篮子类型", () => {
    expect(pcrCsvUrl("csi1000", "index")).toContain("/api/v1/custom-analysis/export/pcr/csi1000.csv?kind=index")
    expect(pcrCsvUrl("star50", "etf")).toContain("/api/v1/custom-analysis/export/pcr/star50.csv?kind=etf")
  })
})

function valuationBody() {
  return {
    data: {
      rules_version: "custom-analysis-v1",
      indicators: [
        {
          code: "erp",
          name: "股债收益差 ERP",
          unit: "百分点",
          definition: "ERP = 100 / PE − 10Y",
          status: "ok",
          zone: "通道内",
          latest: { trade_date: "2026-10-16", value: 3.1, quality_flag: "ok", extras: { pe_ttm: 14.4, note: null } },
          points: [
            { trade_date: "2026-10-15", value: 3.0, band_mean: 3, band_upper: 4, band_lower: 2, quality_flag: "ok" },
            { trade_date: "2026-10-16", value: 3.1, quality_flag: "ok" },
            { trade_date: "2026-10-17", quality_flag: "ok" }, // 没有数值的点必须丢弃
          ],
          inputs: [{ name: "沪深300 PE-TTM", source: "csindex", last_date: "2026-10-16", rows: 1300 }, { rows: 3 }],
          notes: ["说明一", ""],
        },
        { code: "mystery", name: "未知指标" },
        { code: "buffett", name: "巴菲特指标", unit: "%", status: "unavailable", reason: "缺少 GDP" },
      ],
    },
  }
}

describe("估值响应解析", () => {
  it("丢弃未知指标和没有数值的点，保留 extras 的数字与字符串", () => {
    const view = parseValuation(valuationBody())
    expect(view?.indicators.map((item) => item.code)).toEqual(["erp", "buffett"])
    const erp = view?.indicators[0]
    expect(erp?.points).toHaveLength(2)
    expect(erp?.points[1]?.bandUpper).toBeNull()
    expect(erp?.latest?.extras).toEqual({ pe_ttm: 14.4, note: null })
    expect(erp?.inputs).toHaveLength(1)
    expect(erp?.notes).toEqual(["说明一"])
  })

  it("不可用指标保留原因，没有 latest", () => {
    const buffett = parseValuation(valuationBody())?.indicators[1]
    expect(buffett?.status).toBe("unavailable")
    expect(buffett?.reason).toBe("缺少 GDP")
    expect(buffett?.latest).toBeNull()
  })

  it("缺少 data 的响应返回 null", () => {
    expect(parseValuation({})).toBeNull()
  })
})

describe("数据源响应解析", () => {
  it("解析来源行与质量问题，过滤残缺项", () => {
    const view = parseSources({
      data: {
        rules_version: "v1",
        sources: [
          { key: "sse", label: "上交所期权", provider: "SSE", url: "http://x", frequency: "日频", status: "stale", rows: 10, review_rows: 2 },
          { label: "没有 key" },
        ],
        recent_issues: [{ trade_date: "2026-10-16", subject: "SSE 510050", note: "不一致" }, { subject: "无日期" }],
      },
    })
    expect(view?.sources).toHaveLength(1)
    expect(view?.sources[0]?.status).toBe("stale")
    expect(view?.sources[0]?.reviewRows).toBe(2)
    expect(view?.recentIssues).toEqual([{ tradeDate: "2026-10-16", subject: "SSE 510050", note: "不一致" }])
  })
})

function pcrPoint(day: string, patch: Partial<PcrPoint> = {}): PcrPoint {
  return {
    tradeDate: day,
    indexClose: 4000,
    callVolume: 1,
    putVolume: 1,
    callOi: 1,
    putOi: 1,
    volumePcr: 0.8,
    volumePcrMa10: 0.8,
    oiPcr: 0.9,
    percentile5y: 50,
    sentimentSignal: null,
    relativeSignal: null,
    qualityFlag: "ok",
    ...patch,
  }
}

type Series = { name: string; data: (number | null)[]; yAxisIndex?: number; markLine?: { data: { yAxis: number }[] } }

describe("PCR 图表配置", () => {
  it("缺值的点保持 null（断线），不画成 0", () => {
    const option = buildPcrOption([pcrPoint("2026-10-15"), pcrPoint("2026-10-16", { volumePcr: null })], true) as {
      series: Series[]
    }
    expect(option.series[0]?.data).toEqual([0.8, null])
  })

  it("带 0.6 / 1.2 参考线；有指数收盘时才加右轴", () => {
    const withIndex = buildPcrOption([pcrPoint("2026-10-15")], true) as { series: Series[]; yAxis: unknown[] }
    expect(withIndex.series[0]?.markLine?.data.map((item) => item.yAxis)).toEqual([0.6, 1.2])
    expect(withIndex.series.map((item) => item.name)).toContain("跟踪指数收盘")
    expect(withIndex.yAxis).toHaveLength(2)

    const without = buildPcrOption([pcrPoint("2026-10-15", { indexClose: null })], true) as {
      series: Series[]
      yAxis: unknown[]
    }
    expect(without.series.map((item) => item.name)).not.toContain("跟踪指数收盘")
    expect(without.yAxis).toHaveLength(1)
  })

  it("分位图固定 0–100，并标出 10 / 90 线", () => {
    const option = buildPcrPercentileOption([pcrPoint("2026-10-15")]) as {
      yAxis: { min: number; max: number }
      series: Series[]
    }
    expect([option.yAxis.min, option.yAxis.max]).toEqual([0, 100])
    expect(option.series[0]?.markLine?.data.map((item) => item.yAxis)).toEqual([10, 90])
  })

  it("默认只展示最近一年：超过 250 个点才缩放", () => {
    expect(zoomStart(100)).toBe(0)
    expect(zoomStart(VISIBLE_DAYS)).toBe(0)
    expect(zoomStart(VISIBLE_DAYS * 2)).toBe(50)
  })

  it("全是 null 的分位不画图", () => {
    expect(hasPercentiles([{ percentile5y: null }, { percentile5y: null }])).toBe(false)
    expect(hasPercentiles([{ percentile5y: null }, { percentile5y: 12 }])).toBe(true)
  })
})

function indicator(code: Indicator["code"], extras: Record<string, number | string | null> = {}): Indicator {
  return {
    code,
    name: code,
    unit: "x",
    definition: "",
    status: "ok",
    reason: null,
    zone: null,
    percentileNote: null,
    latest: null,
    points: [
      { tradeDate: "2026-10-15", value: 1, percentile5y: 30, bandMean: 1, bandUpper: 2, bandLower: 0, qualityFlag: "ok", extras },
      { tradeDate: "2026-10-16", value: 2, percentile5y: null, bandMean: null, bandUpper: null, bandLower: null, qualityFlag: "ok", extras: {} },
    ],
    inputs: [],
    notes: [],
  }
}

describe("估值图表配置", () => {
  it("ERP 带均值与 ±2σ 通道，样本不足的点保持 null", () => {
    const option = buildIndicatorOption(indicator("erp")) as { series: Series[] }
    expect(option.series.map((item) => item.name)).toEqual([
      "erp",
      "5 年均值",
      "μ+2σ（机会极值）",
      "μ−2σ（风险警戒）",
    ])
    expect(option.series[1]?.data).toEqual([1, null])
  })

  it("样本不足、通道全是 null 时不画通道线也不进图例", () => {
    const sparse = indicator("erp")
    sparse.points = sparse.points.map((point) => ({ ...point, bandMean: null, bandUpper: null, bandLower: null }))
    const option = buildIndicatorOption(sparse) as { series: Series[]; legend: { data: string[] } }
    expect(option.series.map((item) => item.name)).toEqual(["erp"])
    expect(option.legend.data).toEqual(["erp"])
  })

  it("中位数 PB 叠加不剔除 ST 的对照线，缺失保持 null", () => {
    const option = buildIndicatorOption(indicator("median_pb", { median_without_st_filter: 1.2 })) as {
      series: Series[]
    }
    expect(option.series.map((item) => item.name)).toEqual(["median_pb", "不剔除 ST（对照）"])
    expect(option.series[1]?.data).toEqual([1.2, null])
  })

  it("巴菲特指标只画本身；分位图标 20 / 80 线", () => {
    const option = buildIndicatorOption(indicator("buffett")) as { series: Series[] }
    expect(option.series).toHaveLength(1)
    const percentile = buildIndicatorPercentileOption(indicator("buffett")) as { series: Series[] }
    expect(percentile.series[0]?.markLine?.data.map((item) => item.yAxis)).toEqual([20, 80])
    expect(percentile.series[0]?.data).toEqual([30, null])
  })
})
