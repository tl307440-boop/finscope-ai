import { describe, expect, it } from "vitest"
import { bytesToBase64, chartQuestion, sniffChart } from "../../src/api/chartImage"

describe("走势图提问", () => {
  it("只有图时用技能默认问题，两样都没有就不能发", () => {
    expect(chartQuestion("  看一下结构  ", true)).toBe("看一下结构")
    expect(chartQuestion("   ", true)).toBe("请按走势解读技能分析这张图。")
    expect(chartQuestion("   ", false)).toBeNull()
  })
})

describe("走势图文件头", () => {
  it("只认 png、jpeg、gif、webp", () => {
    expect(sniffChart(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))).toBe("image/png")
    expect(sniffChart(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe("image/jpeg")
    expect(sniffChart(new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61]))).toBe("image/gif")
    const webp = new Uint8Array(12)
    webp.set([0x52, 0x49, 0x46, 0x46], 0)
    webp.set([0x57, 0x45, 0x42, 0x50], 8)
    expect(sniffChart(webp)).toBe("image/webp")
    expect(sniffChart(new Uint8Array([0x3c, 0x68, 0x74, 0x6d, 0x6c]))).toBeNull()
  })

  it("字节转成 base64，方便放进提问请求", () => {
    expect(bytesToBase64(new Uint8Array([104, 105]))).toBe("aGk=")
  })
})
