export interface DailyBar {
  trade_date: string
  open: string
  high: string
  low: string
  close: string
  volume: string
  currency: string
  volume_unit: string
  ma5: string | null
  ma10: string | null
  ma20: string | null
  ma60: string | null
  simple_return: string | null
}

export interface CandleMark {
  x: number
  wickTop: number
  wickBottom: number
  bodyY: number
  bodyHeight: number
  rising: boolean
  volumeHeight: number
}

export function adjustmentLabel(adjustment: string): string {
  if (adjustment === "qfq") {
    return "前复权"
  }
  if (adjustment === "hfq") {
    return "后复权"
  }
  return "不复权"
}

export function maLabel(period: number, value: string | null): string {
  if (!value) {
    return `MA${period} 样本不足`
  }
  return `MA${period} ${value}`
}

export function returnLabel(value: string | null): string {
  if (!value) {
    return "收益率：样本不足"
  }
  const ratio = Number(value)
  const percent = (ratio * 100).toFixed(2)
  if (ratio > 0) {
    return `收益率 +${percent}%`
  }
  return `收益率 ${percent}%`
}

export function candleMarks(bars: DailyBar[]): CandleMark[] {
  if (bars.length === 0) {
    return []
  }
  const highs = bars.map((bar) => Number(bar.high))
  const lows = bars.map((bar) => Number(bar.low))
  const top = Math.max(...highs)
  const bottom = Math.min(...lows)
  const span = top - bottom || 1
  const volumes = bars.map((bar) => Number(bar.volume))
  const maxVolume = Math.max(...volumes) || 1
  const width = 280
  const step = width / bars.length
  return bars.map((bar, index) => {
    const open = Number(bar.open)
    const close = Number(bar.close)
    const high = Number(bar.high)
    const low = Number(bar.low)
    const y = (price: number) => 4 + ((top - price) / span) * 92
    const bodyTop = Math.min(y(open), y(close))
    const bodyBottom = Math.max(y(open), y(close))
    return {
      x: index * step + step / 2,
      wickTop: y(high),
      wickBottom: y(low),
      bodyY: bodyTop,
      bodyHeight: Math.max(1, bodyBottom - bodyTop),
      rising: close >= open,
      volumeHeight: (Number(bar.volume) / maxVolume) * 36,
    }
  })
}

/** 后端用英文代码传单位（hand / share），界面一律显示中文。 */
export function volumeUnitLabel(unit: string | null | undefined): string {
  if (!unit) {
    return ""
  }
  const table: Record<string, string> = { hand: "手", share: "股", lot: "份" }
  return table[unit] ?? unit
}

export function summarizeBars(input: { emptyMessage: string | null; bars: DailyBar[] }): string[] {
  if (input.bars.length === 0) {
    return [input.emptyMessage ?? "暂无已同步的日线。未同步的日期不会用演示价格填补。"]
  }
  return input.bars.map((bar) => {
    const unit = volumeUnitLabel(bar.volume_unit)
    return `${bar.trade_date} 开${bar.open} 高${bar.high} 低${bar.low} 收${bar.close} ${bar.currency} 量${bar.volume}${unit}`
  })
}
