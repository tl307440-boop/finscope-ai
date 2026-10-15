export function describeProbeFailure(body: unknown): string {
  const record = asRecord(body)
  const error = asRecord(record?.error)
  const details = asRecord(error?.details)
  const message = asString(error?.message) ?? "上游请求失败"
  if (details?.preserved_existing === true && details.rows_written === 0) {
    return `${message}。已有数据未改动。`
  }
  return message
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}
