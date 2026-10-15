export interface DailyBar {
  trade_date: string
  open: string
  high: string
  low: string
  close: string
  volume: string
  currency: string
  volume_unit: string
}

export function summarizeBars(input: { emptyMessage: string | null; bars: DailyBar[] }): string[] {
  if (input.bars.length === 0) {
    return [input.emptyMessage ?? "暂无已同步的日线。未同步的日期不会用演示价格填补。"]
  }
  return input.bars.map((bar) => {
    const unit = bar.volume_unit === "hand" ? "手" : bar.volume_unit
    return `${bar.trade_date} 开${bar.open} 高${bar.high} 低${bar.low} 收${bar.close} ${bar.currency} 量${bar.volume}${unit}`
  })
}
