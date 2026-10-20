/**
 * 助手正文的 Markdown 子集（规格 §17）：段落、小标题、加粗、列表、表格、行内代码，
 * 以及 `[n]` / `【n】` 引用角标。纯函数，输出的是结构化节点，不是 HTML。
 *
 * 安全性来自结构而不是过滤：解析结果里只有文字，模板用文本插值渲染，
 * 所以 `<script>`、`<img onerror=…>` 这类内容永远只是一行字，没有任何办法变成标签。
 * 不支持的写法（图片、链接、原始 HTML、嵌套列表）按原文显示，不尝试理解。
 *
 * 流式时正文每来一句就整段重新解析；还没写完的 `**` 或表格行按原文显示，写完后自然变成格式。
 */

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "bold"; children: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "cite"; n: number }

export type MdBlock =
  | { kind: "section"; text: string }
  | { kind: "paragraph"; inline: Inline[] }
  | { kind: "list"; ordered: boolean; items: Inline[][] }
  | { kind: "table"; header: Inline[][]; rows: Inline[][][] }

const MAX_TABLE_ROWS = 200
const MAX_TABLE_COLUMNS = 12

// 「一、」「二.」这类中文序号小标题是旧回答的写法，保留。
const SECTION = /^[一二三四五六七八九十]+[、.．]/
const HEADING = /^#{1,6}\s+(.+?)\s*#*\s*$/
const BULLET = /^[-*•·]\s+(.+)$/
const NUMBERED = /^(?:\d{1,2}[.)、]|[（(]\d{1,2}[)）])\s+(.+)$/
const TABLE_ROW = /^\|.*\|?\s*$/
const TABLE_RULE = /^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/
const INLINE = /\*\*(.+?)\*\*|`([^`\n]+)`|\[(\d{1,2})\]|【(\d{1,2})】/g

/** 把一行文字拆成行内节点。加粗里只再认引用和代码，不递归加粗。 */
export function parseInline(text: string, inBold = false): Inline[] {
  const nodes: Inline[] = []
  let cursor = 0
  for (const match of text.matchAll(INLINE)) {
    const at = match.index
    if (match[1] !== undefined && inBold) {
      continue // 加粗里的 ** 不再嵌套：留给后面的文字
    }
    if (at > cursor) {
      nodes.push({ kind: "text", text: text.slice(cursor, at) })
    }
    if (match[1] !== undefined) {
      nodes.push({ kind: "bold", children: parseInline(match[1], true) })
    } else if (match[2] !== undefined) {
      nodes.push({ kind: "code", text: match[2] })
    } else {
      nodes.push({ kind: "cite", n: Number(match[3] ?? match[4]) })
    }
    cursor = at + match[0].length
  }
  if (cursor < text.length) {
    nodes.push({ kind: "text", text: text.slice(cursor) })
  }
  return nodes.length ? nodes : [{ kind: "text", text }]
}

function cells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "")
  return trimmed.split("|").map((cell) => cell.trim())
}

function plain(text: string): string {
  return text.replace(/\*\*(.+?)\*\*/g, "$1")
}

export function parseMarkdown(source: string): MdBlock[] {
  const lines = source.split(/\r?\n/).map((line) => line.trim())
  const blocks: MdBlock[] = []
  let at = 0
  while (at < lines.length) {
    const line = lines[at] ?? ""
    if (!line) {
      at += 1
      continue
    }
    const heading = HEADING.exec(line)
    if (heading?.[1] !== undefined) {
      blocks.push({ kind: "section", text: plain(heading[1]) })
      at += 1
      continue
    }
    if (SECTION.test(line)) {
      blocks.push({ kind: "section", text: plain(line) })
      at += 1
      continue
    }
    if (TABLE_ROW.test(line) && TABLE_RULE.test(lines[at + 1] ?? "")) {
      const width = Math.min(cells(line).length, MAX_TABLE_COLUMNS)
      const header = cells(line).slice(0, width)
      const rows: Inline[][][] = []
      at += 2
      while (at < lines.length && TABLE_ROW.test(lines[at] ?? "") && rows.length < MAX_TABLE_ROWS) {
        const row = cells(lines[at] ?? "").slice(0, width)
        while (row.length < width) {
          row.push("")
        }
        rows.push(row.map((cell) => parseInline(cell)))
        at += 1
      }
      blocks.push({ kind: "table", header: header.map((cell) => parseInline(cell)), rows })
      continue
    }
    const first = BULLET.exec(line) ?? NUMBERED.exec(line)
    if (first) {
      const ordered = !BULLET.test(line)
      const items: Inline[][] = []
      while (at < lines.length) {
        const current = lines[at] ?? ""
        const match = ordered ? NUMBERED.exec(current) : BULLET.exec(current)
        if (!match || match[1] === undefined) {
          break
        }
        items.push(parseInline(match[1]))
        at += 1
      }
      blocks.push({ kind: "list", ordered, items })
      continue
    }
    blocks.push({ kind: "paragraph", inline: parseInline(line) })
    at += 1
  }
  return blocks
}
