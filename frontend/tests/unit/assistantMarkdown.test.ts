import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"
import { parseSources, safeUrl } from "../../src/api/assistantBlocks"
import { parseInline, parseMarkdown } from "../../src/api/assistantMarkdown"
import MarkdownText from "../../src/components/assistant/MarkdownText.vue"
import ReplySources from "../../src/components/assistant/ReplySources.vue"

const SOURCES = parseSources([
  {
    n: 1,
    kind: "corpus",
    publisher: "美联储",
    title: "FOMC 声明",
    published_at: "2026-09-17",
    url: "https://www.federalreserve.gov/x.htm",
    verified: true,
  },
  {
    n: 2,
    kind: "web",
    publisher: "某网站",
    title: "网页文章",
    published_at: "2026-10-01",
    url: "javascript:alert(1)",
    verified: false,
  },
])

describe("Markdown 子集解析", () => {
  it("段落、小标题、列表、表格各归各的", () => {
    const blocks = parseMarkdown(
      [
        "一、结论",
        "## 数据",
        "最新 CPI 为 **3.1%**，见 `E1`。",
        "- 第一点",
        "- 第二点",
        "1. 先看",
        "2. 再看",
        "| 合约组 | 成交量 PCR |",
        "| --- | ---: |",
        "| ETF 期权 | 0.85 |",
        "| 股指期权 | 0.79 |",
      ].join("\n"),
    )
    expect(blocks.map((block) => block.kind)).toEqual([
      "section",
      "section",
      "paragraph",
      "list",
      "list",
      "table",
    ])
    expect(blocks[3]).toMatchObject({ kind: "list", ordered: false })
    expect(blocks[4]).toMatchObject({ kind: "list", ordered: true })
    const table = blocks[5]
    expect(table?.kind === "table" && table.rows).toHaveLength(2)
  })

  it("行内：加粗、代码、引用，其余按原文", () => {
    expect(parseInline("a **b** `c` [1] 【2】 d")).toEqual([
      { kind: "text", text: "a " },
      { kind: "bold", children: [{ kind: "text", text: "b" }] },
      { kind: "text", text: " " },
      { kind: "code", text: "c" },
      { kind: "text", text: " " },
      { kind: "cite", n: 1 },
      { kind: "text", text: " " },
      { kind: "cite", n: 2 },
      { kind: "text", text: " d" },
    ])
    expect(parseInline("没有格式")).toEqual([{ kind: "text", text: "没有格式" }])
  })

  it("还没写完的格式按原文显示：流式过程中不会闪出半截标记之外的东西", () => {
    expect(parseInline("最新 **3.1")).toEqual([{ kind: "text", text: "最新 **3.1" }])
    const partial = parseMarkdown("| 合约组 | 成交量 PCR |")
    expect(partial.map((block) => block.kind)).toEqual(["paragraph"])
  })

  it("数字开头的普通句子不会被当成列表", () => {
    expect(parseMarkdown("3.1% 是最新值。")[0]?.kind).toBe("paragraph")
    expect(parseMarkdown("-5.2% 是跌幅。")[0]?.kind).toBe("paragraph")
    expect(parseMarkdown("2026-10-16 的数据。")[0]?.kind).toBe("paragraph")
  })

  it("表格行短了补空、长了截断，行数有上限", () => {
    const lines = ["| a | b |", "|---|---|", "| 1 |", "| 1 | 2 | 3 |"]
    const table = parseMarkdown(lines.join("\n"))[0]
    expect(table?.kind).toBe("table")
    if (table?.kind !== "table") {
      return
    }
    expect(table.rows.map((row) => row.length)).toEqual([2, 2])
    const many = ["| a |", "|---|", ...Array.from({ length: 500 }, () => "| x |")].join("\n")
    const capped = parseMarkdown(many)[0]
    expect(capped?.kind === "table" && capped.rows.length).toBe(200)
  })

  it("空文本没有块", () => {
    expect(parseMarkdown("")).toEqual([])
    expect(parseMarkdown("\n \n")).toEqual([])
  })
})

describe("MarkdownText 渲染", () => {
  it("格式画成对应的标签", () => {
    const wrapper = mount(MarkdownText, {
      props: { text: "## 结论\n最新 **3.1%**。\n- 要点一\n| a | b |\n|---|---|\n| 1 | 2 |" },
    })
    expect(wrapper.find("h3.doc-section").text()).toBe("结论")
    expect(wrapper.find("p strong").text()).toBe("3.1%")
    expect(wrapper.find("ul.doc-list li").text()).toBe("要点一")
    expect(wrapper.findAll("tbody td").map((cell) => cell.text())).toEqual(["1", "2"])
  })

  it("原始 HTML 只当文字显示，不会变成标签或带上事件", () => {
    const wrapper = mount(MarkdownText, {
      props: {
        text:
          '<img src=x onerror="alert(1)">\n<script>alert(2)</script>\n- <b onclick="x()">粗</b>\n| <i>h</i> |\n|---|\n| <a href="javascript:alert(3)">l</a> |',
      },
    })
    expect(wrapper.find("img").exists()).toBe(false)
    expect(wrapper.find("script").exists()).toBe(false)
    expect(wrapper.find("b").exists()).toBe(false)
    expect(wrapper.find("i").exists()).toBe(false)
    expect(wrapper.find("a").exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror="alert(1)">')
    expect(wrapper.text()).toContain("<script>alert(2)</script>")
    // 看真实的 DOM，而不是 HTML 字符串：文字里本来就会出现 onerror= 这几个字符
    expect(wrapper.element.querySelectorAll("[onerror],[onclick],[href]")).toHaveLength(0)
    expect(wrapper.element.querySelectorAll("p, li, th, td").length).toBeGreaterThan(3)
    expect(wrapper.html()).not.toContain("<img")
    expect(wrapper.html()).not.toContain("<script")
  })

  it("引用对得上来源就画成角标，链接只放行 http(s)", () => {
    const wrapper = mount(MarkdownText, {
      props: { text: "见声明[1]，另见网页[2]，没有的来源[9]。", sources: SOURCES },
    })
    const chips = wrapper.findAll(".cite")
    expect(chips.map((chip) => chip.text())).toEqual(["1", "2"])
    expect(chips[0]?.element.tagName).toBe("A")
    expect(chips[0]?.attributes("href")).toBe("https://www.federalreserve.gov/x.htm")
    expect(chips[0]?.attributes("rel")).toContain("noopener")
    expect(chips[0]?.attributes("title")).toContain("美联储")
    // javascript: 地址不进 href，角标退化成不可点的 span
    expect(chips[1]?.element.tagName).toBe("SPAN")
    expect(chips[1]?.attributes("href")).toBeUndefined()
    expect(chips[1]?.attributes("title")).toContain("未经核验")
    // 没有登记的来源不造角标，按原文显示
    expect(wrapper.text()).toContain("[9]")
  })

  it("没有来源时引用保持原文，旧回答不受影响", () => {
    const wrapper = mount(MarkdownText, { props: { text: "旧回答 [1]。" } })
    expect(wrapper.find(".cite").exists()).toBe(false)
    expect(wrapper.text()).toBe("旧回答 [1]。")
  })
})

describe("来源", () => {
  it("缺编号、重复编号、没有标题和出版方的来源被丢弃；网页来源恒为未核验", () => {
    const parsed = parseSources([
      { n: 1, kind: "web", title: "a", verified: true },
      { n: 1, kind: "corpus", title: "重复" },
      { n: 0, title: "零" },
      { title: "没编号" },
      { n: 3 },
      { n: 4, kind: "corpus", title: "语料" },
      "x",
    ])
    expect(parsed.map((source) => source.n)).toEqual([1, 4])
    expect(parsed[0]?.verified).toBe(false) // 网页来源即使对方说已核验，也不信
    expect(parsed[1]?.verified).toBe(true)
  })

  it("safeUrl 只认 http 和 https", () => {
    expect(safeUrl("https://a.com/x")).toBe("https://a.com/x")
    expect(safeUrl("http://a.com")).toBe("http://a.com/")
    expect(safeUrl("javascript:alert(1)")).toBeNull()
    expect(safeUrl("data:text/html,<b>")).toBeNull()
    expect(safeUrl("//a.com")).toBeNull()
    expect(safeUrl("")).toBeNull()
  })

  it("来源清单：网页来源标未核验，没有来源不显示", () => {
    expect(mount(ReplySources, { props: { sources: [] } }).find(".sources").exists()).toBe(false)
    const wrapper = mount(ReplySources, { props: { sources: SOURCES } })
    const items = wrapper.findAll("li")
    expect(items).toHaveLength(2)
    expect(items[0]?.find("a").attributes("href")).toBe("https://www.federalreserve.gov/x.htm")
    expect(items[1]?.find("a").exists()).toBe(false)
    expect(items[1]?.text()).toContain("未经核验")
  })
})
