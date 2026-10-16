import { describe, expect, it } from "vitest"
import {
  describeJobScope,
  enqueueMessage,
  healthLabel,
  jobStatusLabel,
  providerAccess,
} from "../../src/api/jobs"

describe("同步任务状态", () => {
  it("把队列状态说成中文", () => {
    expect(jobStatusLabel("queued")).toBe("排队")
    expect(jobStatusLabel("running")).toBe("执行中")
    expect(jobStatusLabel("failed")).toBe("失败")
  })

  it("重复提交不会说成新开了一条", () => {
    expect(enqueueMessage(false)).toBe("同一任务已在队列中，没有另开一条")
    expect(enqueueMessage(true)).toBe("已加入队列")
  })

  it("数据源健康有明确的空状态", () => {
    expect(healthLabel(null)).toBe("尚未检查")
    expect(healthLabel("up")).toBe("最近检查正常")
    expect(healthLabel("down")).toBe("最近检查失败")
  })
})

describe("数据源接入方式", () => {
  it("FRED 公开 CSV 算可用，不当成未配置", () => {
    const access = providerAccess({ code: "fred", availability: "public_csv", credentialPresent: false })
    expect(access).toEqual({ mode: "public_csv", text: "公开 CSV", tone: "ok" })
  })

  it("FRED 没有密钥、后端回 not_required 时同样是公开 CSV", () => {
    expect(providerAccess({ code: "fred", availability: "not_required", credentialPresent: false }).mode).toBe(
      "public_csv",
    )
  })

  it("配置了密钥的 FRED 走官方 API", () => {
    expect(providerAccess({ code: "fred", availability: "configured", credentialPresent: true }).text).toBe(
      "已配置密钥",
    )
  })

  it("缺少密钥的数据源标红，免密钥的标绿", () => {
    expect(providerAccess({ code: "tushare", availability: "unavailable", credentialPresent: false })).toEqual({
      mode: "unavailable",
      text: "未配置",
      tone: "danger",
    })
    expect(providerAccess({ code: "akshare", availability: "not_required", credentialPresent: false }).tone).toBe(
      "ok",
    )
  })

  it("不认识的取值按不可用处理", () => {
    expect(providerAccess({ code: "x", availability: "mystery", credentialPresent: false }).mode).toBe("unavailable")
  })
})

describe("同步任务范围", () => {
  it("A 股日线还原成标的与区间", () => {
    expect(describeJobScope("akshare_daily", "akshare:000858.SZ:none:2024-01-02:2024-01-31")).toBe(
      "000858.SZ · 2024-01-02 ~ 2024-01-31",
    )
    expect(describeJobScope("akshare_daily", "akshare:600519.SH:qfq:2024-01-02:2024-01-02")).toBe(
      "600519.SH · 2024-01-02 · 前复权",
    )
  })

  it("FRED 序列还原成序列代码与区间", () => {
    expect(describeJobScope("fred_series", "fred:UNRATE:2020-01-01:2024-12-31")).toBe(
      "UNRATE · 2020-01-01 ~ 2024-12-31",
    )
  })

  it("认不出的去重键不硬拼", () => {
    expect(describeJobScope("akshare_daily", "something-else")).toBe("")
    expect(describeJobScope("unknown", "fred:UNRATE:2020-01-01:2024-12-31")).toBe("")
  })
})
