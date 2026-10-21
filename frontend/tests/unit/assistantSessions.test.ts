import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"
import {
  cleanTitle,
  groupOf,
  groupSessions,
  parseSessions,
  removeSession,
  titleProblem,
  upsertSession,
  type SessionSummary,
} from "../../src/api/assistantSessions"
import SessionList from "../../src/components/assistant/SessionList.vue"

const NOW = new Date(2026, 9, 21, 15, 0)

function at(day: number, hour = 10): string {
  return new Date(2026, 9, day, hour, 0).toISOString()
}

const A: SessionSummary = { id: "a", title: "半导体景气度", updatedAt: at(21) }
const B: SessionSummary = { id: "b", title: "美国 CPI", updatedAt: at(20) }
const C: SessionSummary = { id: "c", title: "茅台估值", updatedAt: at(2) }

describe("parseSessions", () => {
  it("按最近活动倒序，缺 updated_at 时退回 created_at，丢掉没有 id 的条目", () => {
    const parsed = parseSessions({
      data: {
        sessions: [
          { id: "old", title: "旧的", created_at: at(1), updated_at: at(1) },
          { id: "new", title: "新的", created_at: at(3) },
          { title: "没有 id" },
          { id: "untitled", updated_at: at(2) },
        ],
      },
    })
    expect(parsed.map((item) => item.id)).toEqual(["new", "untitled", "old"])
    expect(parsed[0]?.updatedAt).toBe(at(3))
    expect(parsed[1]?.title).toBe("新的问答")
  })

  it("返回体不对时给空列表而不是抛错", () => {
    expect(parseSessions(null)).toEqual([])
    expect(parseSessions({ data: { sessions: "x" } })).toEqual([])
  })
})

describe("列表维护", () => {
  it("upsert 把会话顶到最前且不重复，remove 去掉指定会话", () => {
    const moved = upsertSession([A, B, C], { ...C, title: "改过" })
    expect(moved.map((item) => item.id)).toEqual(["c", "a", "b"])
    expect(moved[0]?.title).toBe("改过")
    expect(removeSession(moved, "a").map((item) => item.id)).toEqual(["c", "b"])
  })

  it("标题压空白，空的和超过 80 字的不收", () => {
    expect(cleanTitle("  半导体   景气 \n 跟踪 ")).toBe("半导体 景气 跟踪")
    expect(titleProblem("   ")).toBe("标题不能为空")
    expect(titleProblem("字".repeat(81))).toBe("标题最多 80 个字")
    expect(titleProblem(" 字".repeat(40))).toBeNull()
  })

  it("按本地日历日分组：今天 / 昨天 / 近 7 天 / 更早", () => {
    expect(groupOf(at(21, 1), NOW)).toBe("今天")
    expect(groupOf(at(20, 23), NOW)).toBe("昨天")
    expect(groupOf(at(15), NOW)).toBe("近 7 天")
    expect(groupOf(at(2), NOW)).toBe("更早")
    expect(groupOf("坏时间", NOW)).toBe("更早")
    expect(groupSessions([A, B, C], NOW).map((block) => [block.group, block.items.length])).toEqual([
      ["今天", 1],
      ["昨天", 1],
      ["更早", 1],
    ])
  })
})

function mountList(props: Partial<InstanceType<typeof SessionList>["$props"]> = {}) {
  return mount(SessionList, {
    props: { sessions: [A, B, C], activeId: "a", phase: "ready", ...props },
  })
}

describe("SessionList", () => {
  it("点别的会话发 select，点当前会话不发", async () => {
    const wrapper = mountList()
    await wrapper.get('[data-session="b"] .pick').trigger("click")
    await wrapper.get('[data-session="a"] .pick').trigger("click")
    expect(wrapper.emitted("select")).toEqual([["b"]])
    expect(wrapper.get('[data-session="a"] .pick').attributes("aria-current")).toBe("true")
  })

  it("生成中锁住：不能切换、改名、删除、新建", async () => {
    const wrapper = mountList({ locked: true })
    expect(wrapper.text()).toContain("回答生成中")
    expect(wrapper.get('[data-session="b"] .pick').attributes("disabled")).toBeDefined()
    expect(wrapper.get(".new").attributes("disabled")).toBeDefined()
    await wrapper.get('[data-session="b"] .pick').trigger("click")
    await wrapper.get('[data-session="b"] [aria-label^="删除"]').trigger("click")
    expect(wrapper.emitted("select")).toBeUndefined()
    expect(wrapper.find(".confirm").exists()).toBe(false)
  })

  it("改名：原位编辑，空标题提示原因不提交，合法标题压空白后提交", async () => {
    const wrapper = mountList()
    await wrapper.get('[data-session="b"] [aria-label^="重命名"]').trigger("click")
    const input = wrapper.get(".rename-input")
    await input.setValue("   ")
    await wrapper.get(".rename").trigger("submit")
    expect(wrapper.text()).toContain("标题不能为空")
    expect(wrapper.emitted("rename")).toBeUndefined()

    await input.setValue("  美国   通胀 ")
    await wrapper.get(".rename").trigger("submit")
    expect(wrapper.emitted("rename")).toEqual([["b", "美国 通胀"]])
    expect(wrapper.find(".rename").exists()).toBe(false)
  })

  it("删除要再确认一次，取消就什么也不发", async () => {
    const wrapper = mountList()
    await wrapper.get('[data-session="c"] [aria-label^="删除"]').trigger("click")
    expect(wrapper.get(".confirm").text()).toContain("删除「茅台估值」及其中全部消息")
    await wrapper.get(".confirm .link:not(.danger)").trigger("click")
    expect(wrapper.emitted("remove")).toBeUndefined()

    await wrapper.get('[data-session="c"] [aria-label^="删除"]').trigger("click")
    await wrapper.get(".confirm .danger").trigger("click")
    expect(wrapper.emitted("remove")).toEqual([["c"]])
  })

  it("loading / empty / error 三种状态，error 可重试", async () => {
    expect(mountList({ phase: "loading" }).find('[aria-busy="true"]').exists()).toBe(true)
    expect(mountList({ sessions: [] }).text()).toContain("还没有问答记录")

    const failed = mountList({ phase: "error", message: "问答记录读不出来" })
    expect(failed.get('[role="alert"]').text()).toContain("问答记录读不出来")
    await failed.get(".retry").trigger("click")
    expect(failed.emitted("retry")).toHaveLength(1)
  })

  it("改名或删除失败时显示父组件给的原因", () => {
    const wrapper = mountList({ actionError: "这个问答里还有回答正在生成，请等它结束或取消后再删除" })
    expect(wrapper.get(".action-error").text()).toContain("正在生成")
  })
})
