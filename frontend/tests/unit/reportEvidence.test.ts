import { describe, expect, it } from "vitest"
import { evidenceLinks, markdownFilename } from "../../src/api/reportEvidence"

const body = [
  "us-gaap:Revenues USD 早期 80 申报号 0000320193-23-000001 来源 https://data.sec.gov/example.json 事实 abc",
  "后期 120 申报号 0000320193-24-000099 来源 https://data.sec.gov/example.json 事实 def",
].join(" ")

describe("报告证据", () => {
  it("从正文抽出可核对的数字和申报号", () => {
    const links = evidenceLinks(body)
    expect(links.map((item) => item.value)).toEqual(["80", "120"])
    expect(links[0]?.accession).toBe("0000320193-23-000001")
    expect(links[1]?.sourceUrl).toContain("data.sec.gov")
    expect(evidenceLinks("没有数字")).toEqual([])
  })

  it("导出文件名去掉路径字符", () => {
    expect(markdownFilename("苹果 研究记录")).toBe("苹果 研究记录.md")
    expect(markdownFilename("a/b")).toBe("ab.md")
    expect(markdownFilename("   ")).toBe("研究记录.md")
  })
})