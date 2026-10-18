import { describe, expect, it } from "vitest"
import { PBOC_LINKS, PBOC_SERIES, expectedMonth, importStatus } from "../../src/api/chinaImport"

// 北京时间 = UTC + 8
const at = (iso: string) => new Date(`${iso}+08:00`)

describe("人民银行月度导入提醒", () => {
  it("16 日及以后应有上月数据，15 日及以前应有上上月", () => {
    expect(expectedMonth(at("2026-10-18T10:00:00"))).toBe("2026-09")
    expect(expectedMonth(at("2026-10-16T00:00:00"))).toBe("2026-09")
    expect(expectedMonth(at("2026-10-15T23:59:00"))).toBe("2026-08")
    expect(expectedMonth(at("2026-10-01T08:00:00"))).toBe("2026-08")
  })

  it("跨年：1 月中旬前应有 11 月，1 月 16 日后应有 12 月", () => {
    expect(expectedMonth(at("2026-01-10T08:00:00"))).toBe("2025-11")
    expect(expectedMonth(at("2026-01-20T08:00:00"))).toBe("2025-12")
    expect(expectedMonth(at("2026-02-03T08:00:00"))).toBe("2025-12")
  })

  it("按北京时间取日期，不受本地时区影响", () => {
    // UTC 15 日 17:00 = 北京时间 16 日 01:00
    expect(expectedMonth(new Date("2026-10-15T17:00:00Z"))).toBe("2026-09")
    expect(expectedMonth(new Date("2026-10-15T16:00:00Z"))).toBe("2026-09")
    expect(expectedMonth(new Date("2026-10-15T15:59:00Z"))).toBe("2026-08")
  })

  it("最新观测月达到应有月份就是最新，落后则待导入", () => {
    const now = at("2026-10-18T10:00:00")
    expect(importStatus("2026-09-01", now).state).toBe("current")
    expect(importStatus("2026-10-01", now).state).toBe("current")
    const due = importStatus("2026-08-01", now)
    expect(due.state).toBe("due")
    expect(due.text).toContain("应有 2026-09")
  })

  it("没有数据或日期格式不对时按缺失处理，不猜测", () => {
    const now = at("2026-10-18T10:00:00")
    expect(importStatus(null, now).state).toBe("missing")
    expect(importStatus(undefined, now).state).toBe("missing")
    expect(importStatus("", now).state).toBe("missing")
    expect(importStatus("2026/09", now).state).toBe("missing")
    expect(importStatus(null, now).latest).toBeNull()
  })

  it("三个序列与官方链接是固定配置，链接是 https 的人民银行域名", () => {
    expect(PBOC_SERIES.map((item) => item.code)).toEqual([
      "SOCIAL_FINANCING_STOCK_CN",
      "GOV_BOND_NET_CN",
      "ENT_MLT_LOAN_CN",
    ])
    expect(PBOC_LINKS.length).toBeGreaterThanOrEqual(2)
    for (const link of PBOC_LINKS) {
      expect(new URL(link.url).hostname).toBe("www.pbc.gov.cn")
      expect(link.url.startsWith("https://")).toBe(true)
    }
  })
})
