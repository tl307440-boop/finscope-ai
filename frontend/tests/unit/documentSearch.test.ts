import { describe, expect, it } from "vitest"
import { pageLabel } from "../../src/api/documentSearch"

describe("PDF 检索页码", () => {
  it("同一页和跨页都标出页码", () => {
    expect(pageLabel(2, 2)).toBe("第 2 页")
    expect(pageLabel(1, 2)).toBe("第 1–2 页")
  })
})