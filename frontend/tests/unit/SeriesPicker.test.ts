import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"
import SeriesPicker from "../../src/components/ui/SeriesPicker.vue"

const options = [
  { code: "A", label: "指标 A", color: "#3b82f6" },
  { code: "B", label: "指标 B", color: "#22d3ee" },
  { code: "C", label: "指标 C", color: "#a78bfa" },
  { code: "D", label: "指标 D", color: "#fbbf24", disabled: true },
]

function build(modelValue: string[]) {
  return mount(SeriesPicker, { props: { options, modelValue, years: 2, yearOptions: [1, 2, 3] } })
}

describe("SeriesPicker 图例选择器", () => {
  it("可加入任意数量的指标，并保持选项原有顺序", async () => {
    const wrapper = build(["C"])
    await wrapper.findAll(".chip")[0]!.trigger("click")
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([["A", "C"]])
  })

  it("再次点击已选指标会移除它", async () => {
    const wrapper = build(["A", "B", "C"])
    await wrapper.findAll(".chip")[1]!.trigger("click")
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([["A", "C"]])
  })

  it("没有数据的指标不可选，全选也会跳过它", async () => {
    const wrapper = build([])
    await wrapper.findAll(".chip")[3]!.trigger("click")
    expect(wrapper.emitted("update:modelValue")).toBeUndefined()
    await wrapper.findAll(".link")[0]!.trigger("click")
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([["A", "B", "C"]])
  })

  it("清空与切换时间窗口", async () => {
    const wrapper = build(["A"])
    await wrapper.findAll(".link")[1]!.trigger("click")
    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual([[]])
    await wrapper.findAll(".range")[2]!.trigger("click")
    expect(wrapper.emitted("update:years")?.[0]).toEqual([3])
  })
})
