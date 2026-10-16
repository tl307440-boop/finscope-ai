import { describe, expect, it } from "vitest"
import { documentStatus, uploadStatusText } from "../../src/api/uploads"

describe("PDF 上传状态", () => {
  it("重复、扫描件和损坏文件各有明确说明", () => {
    expect(uploadStatusText("duplicate", "")).toBe("这份文件已经上传过")
    expect(uploadStatusText("ocr_required", "")).toContain("扫描件")
    expect(uploadStatusText("failed", "")).toContain("不会进入解析")
    expect(uploadStatusText("queued", "")).toBe("已接收，等待解析")
  })
})

describe("已上传文件的解析状态", () => {
  it("只有解析完成的文件标为可检索", () => {
    expect(documentStatus("parsed")).toEqual({ text: "可检索", tone: "ok" })
    expect(documentStatus("queued").tone).toBe("warn")
    expect(documentStatus("parsing").text).toBe("解析中")
  })

  it("扫描件与失败文件不显示成可用", () => {
    expect(documentStatus("ocr_required")).toEqual({ text: "需要文字版", tone: "warn" })
    expect(documentStatus("failed")).toEqual({ text: "解析失败", tone: "danger" })
  })

  it("不认识的取值不回显英文枚举", () => {
    expect(documentStatus("archived")).toEqual({ text: "状态未知", tone: "muted" })
  })
})
