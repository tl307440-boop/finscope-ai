import { describe, expect, it } from "vitest"
import { uploadStatusText } from "../../src/api/uploads"

describe("PDF 上传状态", () => {
  it("重复、扫描件和损坏文件各有明确说明", () => {
    expect(uploadStatusText("duplicate", "")).toBe("这份文件已经上传过")
    expect(uploadStatusText("ocr_required", "")).toContain("扫描件")
    expect(uploadStatusText("failed", "")).toContain("不会进入解析")
    expect(uploadStatusText("queued", "")).toBe("已接收，等待解析")
  })
})
