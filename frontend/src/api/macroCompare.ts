/**
 * 多指标对比分析：原值（双轴）与指数化（2020-01 = 100）两种视图，以及 CSV 导出。纯函数。
 *
 * 不编造数据（AGENTS.md）：
 *  - 指数化只对「水平值」序列有意义（价格指数、就业人数、GDP 水平值）；同比 %、利率、景气指数不能指数化，
 *    选中时给出原因并跳过，不硬算。
 *  - 基期（2020-01）当月没有观测的序列同样跳过；不用邻月替代。
 *  - 不同频率各画各的观测点，不插值、不补点；原值视图最多同时显示两种单位（左右各一根轴）。
 */
import { shiftYears } from "./macroStats"
import type { Observation } from "./macroTiles"

export const BASE_MONTH = "2020-01"

export type CompareTone =
  | "accent"
  | "orange"
  | "violet"
  | "warn"
  | "ok"
  | "cyan"
  | "pink"
  | "muted"
  | "teal"
  | "sky"

export interface CompareSpec {
  code: string
  label: string
  /** 单位文字，用于分轴与导出表头，如「%」「点」「千人」 */
  unit: string
  /** 水平值序列才可以指数化 */
  indexable: boolean
  tone: CompareTone
}

export type CompareMode = "raw" | "index"

export interface CompareLine {
  spec: CompareSpec
  rows: Observation[]
  /** 0 = 左轴，1 = 右轴；指数化视图全部在左轴 */
  axis: 0 | 1
}

export interface CompareSkip {
  code: string
  label: string
  reason: string
}

export interface CompareResult {
  mode: CompareMode
  lines: CompareLine[]
  skipped: CompareSkip[]
  /** 原值视图左右轴的单位；指数化视图为 ["指数"] */
  axisUnits: string[]
}

type Rows = Readonly<Record<string, readonly Observation[]>>

/** 取基期当月第一条观测作为 100；基期值必须是有限正数。 */
export function indexRows(rows: readonly Observation[], base: string = BASE_MONTH): Observation[] | null {
  const anchor = rows.find((row) => row.date.slice(0, 7) === base)
  if (!anchor || !Number.isFinite(anchor.value) || anchor.value <= 0) {
    return null
  }
  return rows
    .filter((row) => row.date >= anchor.date)
    .map((row) => ({ date: row.date, value: (row.value / anchor.value) * 100 }))
}

function newestDate(lines: readonly { rows: readonly Observation[] }[]): string {
  return lines.reduce((high, line) => {
    const last = line.rows[line.rows.length - 1]?.date ?? ""
    return last > high ? last : high
  }, "")
}

/** years = 0 表示全部历史；指数化视图固定从基期开始，忽略 years。 */
export function buildCompare(
  rowsByCode: Rows,
  specs: readonly CompareSpec[],
  codes: readonly string[],
  mode: CompareMode,
  years: number,
): CompareResult {
  const picked = specs.filter((spec) => codes.includes(spec.code))
  const skipped: CompareSkip[] = []
  const usable: { spec: CompareSpec; rows: Observation[] }[] = []
  for (const spec of picked) {
    const rows = [...(rowsByCode[spec.code] ?? [])]
    if (mode === "index") {
      if (!spec.indexable) {
        skipped.push({ code: spec.code, label: spec.label, reason: "不是水平值（同比、利率或景气指数），不能指数化" })
        continue
      }
      const indexed = indexRows(rows)
      if (!indexed) {
        skipped.push({ code: spec.code, label: spec.label, reason: `缺少 ${BASE_MONTH} 的基期数据` })
        continue
      }
      usable.push({ spec, rows: indexed })
      continue
    }
    if (rows.length < 2) {
      skipped.push({ code: spec.code, label: spec.label, reason: "已公布数据不足两期" })
      continue
    }
    usable.push({ spec, rows })
  }
  if (mode === "index") {
    return {
      mode,
      lines: usable.map((entry) => ({ ...entry, axis: 0 as const })),
      skipped,
      axisUnits: ["指数"],
    }
  }
  const units = [...new Set(usable.map((entry) => entry.spec.unit))]
  const keep = new Set(units.slice(0, 2))
  const windowed = usable
    .filter((entry) => {
      if (keep.has(entry.spec.unit)) {
        return true
      }
      skipped.push({ code: entry.spec.code, label: entry.spec.label, reason: "最多同时显示两种单位" })
      return false
    })
    .map((entry) => ({ ...entry }))
  const cutoff = years > 0 && windowed.length ? shiftYears(newestDate(windowed), years) : ""
  const axisUnits = [...keep]
  return {
    mode,
    lines: windowed.map((entry) => ({
      spec: entry.spec,
      rows: cutoff ? entry.rows.filter((row) => row.date >= cutoff) : entry.rows,
      axis: (axisUnits.indexOf(entry.spec.unit) === 1 ? 1 : 0) as 0 | 1,
    })),
    skipped,
    axisUnits,
  }
}

function csvCell(text: string): string {
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** 所有序列的观测日取并集、按日期升序；某序列在该日没有观测则留空（不插值）。 */
export function toCsv(result: CompareResult): string {
  const unit = (line: CompareLine): string => (result.mode === "index" ? `指数，${BASE_MONTH}=100` : line.spec.unit)
  const header = ["日期", ...result.lines.map((line) => csvCell(`${line.spec.label}（${unit(line)}）`))]
  const dates = [...new Set(result.lines.flatMap((line) => line.rows.map((row) => row.date)))].sort()
  const lookup = result.lines.map((line) => new Map(line.rows.map((row) => [row.date, row.value])))
  const body = dates.map((date) =>
    [date, ...lookup.map((map) => (map.has(date) ? String(Number((map.get(date) as number).toFixed(4))) : ""))].join(","),
  )
  return [header.join(","), ...body].join("\n")
}
