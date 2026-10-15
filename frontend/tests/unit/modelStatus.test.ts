import { describe, expect, it } from "vitest"
import { modelNotice } from "../../src/api/modelStatus"

describe("模型状态", () => {
  it("未配置时不生成段落，已配置时仍要求引用校验", () => {
    expect(modelNotice("未配置模型，不会生成 AI 段落", false)).toContain("不会生成")
    expect(modelNotice("模型已配置。输出未经引用校验前，不得视为已核实。", true)).toContain(
      "不能当成已核实",
    )
    expect(modelNotice(null, false)).toBe("无法确认模型状态")
  })
})