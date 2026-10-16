/**
 * 统一的 JSON 取值助手。
 * 规格书要求窄类型 + 运行时校验，禁止把 unknown 直接断言成业务类型，
 * 因此所有接口返回一律经过这里的收窄函数再进入视图。
 */

export function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

export function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}

export function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function asBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null
}

export function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

/** 取出 success_response 包裹里的 data 对象。 */
export function dataOf(body: unknown): Record<string, unknown> | null {
  return asRecord(asRecord(body)?.data)
}

export function listOf(value: unknown): Record<string, unknown>[] {
  return asList(value).flatMap((item) => {
    const record = asRecord(item)
    return record ? [record] : []
  })
}

/** 解析带时区的 ISO 时间，失败返回 null。 */
export function asDate(value: unknown): Date | null {
  const text = asString(value)
  if (!text) {
    return null
  }
  const parsed = new Date(text)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** 格式化为本地 yyyy-MM-dd HH:mm（UTC 存储，界面本地展示）。 */
export function formatMoment(value: unknown): string {
  const parsed = asDate(value)
  if (!parsed) {
    return "—"
  }
  const pad = (part: number): string => String(part).padStart(2, "0")
  return (
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}` +
    ` ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
  )
}

/** 数值格式化：null 显示占位符，永不显示 NaN。 */
export function formatNumber(value: number | null, digits = 2): string {
  if (value === null) {
    return "—"
  }
  return value.toFixed(digits)
}

/** 大额数值按中文量级缩写：1.2e10 → { text: "120.00", scale: "亿" }；小于 1 万原样保留。 */
export function scaleAmount(value: number | null, digits = 2): { text: string; scale: string } {
  if (value === null || !Number.isFinite(value)) {
    return { text: "—", scale: "" }
  }
  const size = Math.abs(value)
  const steps: [number, string][] = [
    [1e12, "万亿"],
    [1e8, "亿"],
    [1e4, "万"],
  ]
  for (const [base, scale] of steps) {
    if (size >= base) {
      return { text: (value / base).toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits }), scale }
    }
  }
  return { text: value.toLocaleString("zh-CN", { maximumFractionDigits: digits }), scale: "" }
}

/** scaleAmount 的字符串版：附带单位，例如 formatAmount(2.18e10, "美元") → "218.37 亿美元"。 */
export function formatAmount(value: number | null, unit = "", digits = 2): string {
  const { text, scale } = scaleAmount(value, digits)
  return text === "—" ? text : `${text} ${scale}${unit}`.trim()
}
