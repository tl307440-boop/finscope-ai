export type Availability = "unavailable" | "configured" | "not_required"

export interface MacroSeriesItem {
  series_code: string
  name_cn: string
  name_en: string
  frequency: string
  base_unit: string
  seasonal_adjustment: string
}

export interface MacroCatalog {
  availability: Availability
  notice: string
  series: MacroSeriesItem[]
}

export interface MacroObservation {
  observation_date: string
  value: string
  unit: string
  realtime_start: string
  revision_no: number
}

export function providerStatusLabel(input: {
  credentialRequired: boolean
  availability: string
}): string {
  if (input.availability === "unavailable") {
    return "不可用"
  }
  if (!input.credentialRequired) {
    return "不需要密钥"
  }
  return "已提供密钥"
}

export function describeObservations(input: {
  emptyMessage: string | null
  observations: MacroObservation[]
}): string[] {
  if (input.observations.length === 0) {
    return [input.emptyMessage ?? "该日期还看不到已公布的数值"]
  }
  return input.observations.map(
    (item) =>
      `${item.observation_date} · ${item.value} ${item.unit} · 发布日次日可见 ${item.realtime_start} · 第 ${item.revision_no} 次`,
  )
}
