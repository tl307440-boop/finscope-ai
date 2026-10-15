import { describe, expect, it } from "vitest"
import { enqueueMessage, healthLabel, jobStatusLabel } from "../../src/api/jobs"

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
