import { asNumber, asString, dataOf, listOf } from "./json"
import { conceptLabel, formatConcept } from "./secConcepts"

/** 板块强弱至少要 3 只成分有对齐的日线才计算，与后端 MIN_SECTOR_NAMES 一致。 */
export const MIN_SECTOR_MEMBERS = 3

export const STRENGTH_RULE =
  "强弱分数 = 50 + 成分股区间收益中位数相对沪深300的超额 × 100，限定在 0–100；≥60 偏强，≤40 偏弱。" +
  `至少 ${MIN_SECTOR_MEMBERS} 只成分股有完整日线才计算。`

export interface SectorStrength {
  name: string
  scored: boolean
  label: string
  score: number | null
  members: number | null
  notice: string
}

export function parseSectors(body: unknown): SectorStrength[] {
  return listOf(dataOf(body)?.sectors).flatMap((row) => {
    const name = asString(row.name)
    if (!name) {
      return []
    }
    const score = asNumber(row.score)
    const scored = asString(row.status) === "ok" && score !== null
    const members = Number((asString(row.coverage) ?? "").match(/成分\s*(\d+)\s*只/)?.[1] ?? Number.NaN)
    return [
      {
        name,
        scored,
        label: scored ? (asString(row.label) ?? "") : "样本不足",
        score: scored ? score : null,
        members: Number.isFinite(members) ? members : null,
        notice: asString(row.notice) ?? "",
      },
    ]
  })
}

/** 一句话说明强弱结论；样本不足时写清门槛，而不是「无法判断」四个字。 */
export function strengthCaption(row: SectorStrength | null | undefined): string {
  if (!row) {
    return "暂无强弱数据"
  }
  if (!row.scored) {
    return `样本不足 ${MIN_SECTOR_MEMBERS} 只，暂不判断`
  }
  return row.members === null ? row.label : `${row.label} · 成分 ${row.members} 只`
}

/** 偏强按「涨」着色、偏弱按「跌」着色（红涨绿跌），中性用主色，样本不足置灰。 */
export function strengthTone(row: SectorStrength | null | undefined): "up" | "down" | "accent" | "muted" {
  if (!row?.scored) {
    return "muted"
  }
  if (row.label === "偏强") {
    return "up"
  }
  return row.label === "偏弱" ? "down" : "accent"
}

export function industryFamily(code: string): string {
  return code.startsWith("sw-") ? "申万一级" : "目录分类"
}

export interface FactView {
  label: string
  display: string
  accession: string
}

/** 事实原文形如「us-gaap:Assets 352583000000 USD 申报号 0000320193-24-000123」。 */
const FACT_QUOTE = /^(\S+:\S+) (-?\d+(?:\.\d+)?) (\S+)(?: 申报号 (\S+))?$/

export function parseFactQuote(quote: string): FactView | null {
  const match = FACT_QUOTE.exec(quote.trim())
  if (!match) {
    return null
  }
  const value = Number(match[2])
  return {
    label: conceptLabel(match[1] ?? ""),
    display: formatConcept(Number.isFinite(value) ? value : null, match[3] ?? ""),
    accession: match[4] ?? "",
  }
}

/** 解析不了的原文照常展示，只把能识别的科目换成中文与带量级的数字。 */
export function factText(quote: string): string {
  const view = parseFactQuote(quote)
  return view ? `${view.label} ${view.display}` : quote
}

export function hostLabel(url: string): string {
  try {
    const host = new URL(url).hostname
    if (host.endsWith("sec.gov")) {
      return "SEC EDGAR"
    }
    if (host.endsWith("cninfo.com.cn")) {
      return "巨潮资讯"
    }
    return host
  } catch {
    return ""
  }
}
