<script setup lang="ts">
/**
 * 个股 / 指数行情（规格书 §1.3 `/market/stocks/:symbol`）。
 *
 * 组成：PageHeader（名称 + 代码）+ 日K（ECharts 蜡烛图 + 均线 + 成交量副图）
 * + 当日 OHLC / 均线 / 区间表现 + 日线明细。
 * 后端日线接口只提供 1d 周期，所以本页只有日K，不用日线拼出周月线。
 * 缺失交易日不补 0，观察日之后的 K 线不显示。涨跌色按 A 股习惯红涨绿跌
 * （统一走 DeltaValue），并同时用 +/- 标注，兼顾色盲模式。
 */
import { computed, ref, watch } from "vue"
import { useRouter } from "vue-router"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { DataLine, Histogram, TrendCharts } from "@element-plus/icons-vue"
import { adjustmentLabel, volumeUnitLabel, type DailyBar } from "../api/bars"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, formatAmount, scaleAmount } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import DeltaValue from "../components/ui/DeltaValue.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const props = defineProps<{ symbol?: string }>()
const router = useRouter()

const MA_WINDOWS = [5, 10, 20, 60] as const
type MaWindow = (typeof MA_WINDOWS)[number]
const MA_FIELD: Record<MaWindow, "ma5" | "ma10" | "ma20" | "ma60"> = {
  5: "ma5",
  10: "ma10",
  20: "ma20",
  60: "ma60",
}
const MA_COLOR: Record<MaWindow, string> = {
  5: "#fbbf24",
  10: "#60a5fa",
  20: "#c084fc",
  60: "#22d3ee",
}
/** 与主题 candlestick 默认色一致：红涨绿跌。 */
const RISE = "#f6465d"
const FALL = "#1fc37d"

/** 已入库的四个 A 股指数，可一键切换。 */
const INDEX_TABS = [
  { key: "000001.SH", label: "上证指数" },
  { key: "000300.SH", label: "沪深300" },
  { key: "399001.SZ", label: "深证成指" },
  { key: "399006.SZ", label: "创业板指" },
] as const

const EXCHANGE_LABEL: Record<string, string> = {
  SSE: "上交所",
  SZSE: "深交所",
  BSE: "北交所",
  NASDAQ: "纳斯达克",
  NYSE: "纽交所",
}
const TYPE_LABEL: Record<string, string> = { STOCK: "股票", INDEX: "指数", ETF: "ETF" }

const symbol = ref((props.symbol ?? "600519.SH").toUpperCase())
const codeInput = ref(symbol.value)
const activeMas = ref<MaWindow[]>([5, 10, 20])
const adjustment = ref("none")
const range = ref<string[] | null>(null)
const asOf = ref("")

const instrument = ref<InstrumentHit | null>(null)
const bars = ref<DailyBar[]>([])
const emptyMessage = ref<string | null>(null)
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")

const displayName = computed(() => {
  const item = instrument.value
  return item ? item.nameCn || item.nameEn || item.displaySymbol : ""
})

const heading = computed(() => {
  const item = instrument.value
  if (!item) {
    return symbol.value
  }
  return `${displayName.value} ${item.displaySymbol || item.canonicalCode}`
})

const subtitle = computed(() => {
  const item = instrument.value
  if (!item) {
    return "日K线行情"
  }
  const parts = [EXCHANGE_LABEL[item.exchange] ?? item.exchange, TYPE_LABEL[item.instrumentType] ?? "", item.currency]
  return parts.filter(Boolean).join(" · ")
})

const activeIndexTab = computed(() =>
  INDEX_TABS.some((tab) => tab.key === symbol.value) ? symbol.value : "",
)

const latest = computed<DailyBar | null>(() => bars.value.at(-1) ?? null)
const previous = computed<DailyBar | null>(() => bars.value.at(-2) ?? null)
const volumeUnit = computed(() => volumeUnitLabel(latest.value?.volume_unit))
const currency = computed(() => instrument.value?.currency ?? latest.value?.currency ?? "CNY")

const change = computed(() => {
  const close = asNumber(latest.value?.close ?? null)
  const prior = asNumber(previous.value?.close ?? null)
  if (close === null || prior === null || prior === 0) {
    return null
  }
  const delta = close - prior
  return { delta, percent: (delta / prior) * 100 }
})

function priceText(value: number | null): string {
  if (value === null) {
    return "—"
  }
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rawPrice(value: string | null | undefined): string {
  return priceText(asNumber(value ?? null))
}

function compactVolume(value: number): string {
  const { text, scale } = scaleAmount(value, value >= 1e8 ? 1 : 0)
  return `${text}${scale}`
}

/** 当日读数：只用最新一根已入库日线，不补价。 */
const ohlc = computed(() => {
  const bar = latest.value
  if (!bar) {
    return []
  }
  return [
    { label: "开盘", value: rawPrice(bar.open) },
    { label: "最高", value: rawPrice(bar.high) },
    { label: "最低", value: rawPrice(bar.low) },
    { label: "收盘", value: rawPrice(bar.close) },
  ]
})

const maReadings = computed(() => {
  const bar = latest.value
  const close = asNumber(bar?.close ?? null)
  return MA_WINDOWS.map((window) => {
    const value = bar ? asNumber(bar[MA_FIELD[window]]) : null
    return {
      window,
      color: MA_COLOR[window],
      value,
      gap: value !== null && close !== null && value !== 0 ? ((close - value) / value) * 100 : null,
    }
  })
})

/** 区间表现：全部由当前返回的日线算出。 */
const rangeStats = computed(() => {
  const rows = bars.value
  if (!rows.length) {
    return null
  }
  let high: { value: number; date: string } | null = null
  let low: { value: number; date: string } | null = null
  let volumeSum = 0
  let volumeCount = 0
  for (const bar of rows) {
    const h = asNumber(bar.high)
    const l = asNumber(bar.low)
    const v = asNumber(bar.volume)
    if (h !== null && (high === null || h > high.value)) {
      high = { value: h, date: bar.trade_date }
    }
    if (l !== null && (low === null || l < low.value)) {
      low = { value: l, date: bar.trade_date }
    }
    if (v !== null) {
      volumeSum += v
      volumeCount += 1
    }
  }
  const first = asNumber(rows[0]?.close ?? null)
  const last = asNumber(rows.at(-1)?.close ?? null)
  return {
    from: rows[0]?.trade_date ?? "",
    to: rows.at(-1)?.trade_date ?? "",
    percent: first && last !== null ? ((last - first) / first) * 100 : null,
    high,
    low,
    avgVolume: volumeCount ? volumeSum / volumeCount : null,
    days: rows.length,
  }
})

/** 明细表按交易日倒序，最新的在最上面。 */
const tableRows = computed(() => [...bars.value].reverse())

function returnPercent(row: DailyBar): number | null {
  const value = asNumber(row.simple_return)
  return value === null ? null : value * 100
}

const chartOption = computed<EChartsOption>(() => {
  const rows = bars.value
  const dates = rows.map((row) => row.trade_date)
  const candles = rows.map((row) => [asNumber(row.open), asNumber(row.close), asNumber(row.low), asNumber(row.high)])
  const volumes = rows.map((row) => ({
    value: asNumber(row.volume),
    itemStyle: {
      color: (asNumber(row.close) ?? 0) >= (asNumber(row.open) ?? 0) ? "rgba(246,70,93,0.6)" : "rgba(31,195,125,0.6)",
    },
  }))
  const unit = volumeUnit.value
  const windows = [...activeMas.value].sort((a, b) => a - b)

  const maSeries = windows.map((window) => ({
    name: `MA${window}`,
    type: "line" as const,
    data: rows.map((row) => asNumber(row[MA_FIELD[window]])),
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 1.2, color: MA_COLOR[window] },
    itemStyle: { color: MA_COLOR[window] },
    connectNulls: false,
  }))

  return {
    animation: false,
    legend: { top: 0, left: 0, data: ["K线", ...windows.map((window) => `MA${window}`)] },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter: (raw: unknown) => {
        const list = Array.isArray(raw) ? raw : [raw]
        const index = asNumber(asRecord(list[0])?.dataIndex) ?? -1
        const bar = rows[index]
        if (!bar) {
          return ""
        }
        const volume = asNumber(bar.volume)
        const ret = returnPercent(bar)
        const lines = [
          `<b>${bar.trade_date}</b>`,
          `开 ${rawPrice(bar.open)}　高 ${rawPrice(bar.high)}`,
          `低 ${rawPrice(bar.low)}　收 ${rawPrice(bar.close)}`,
          `涨跌幅 ${ret === null ? "—" : `${ret > 0 ? "+" : ""}${ret.toFixed(2)}%`}`,
          `成交量 ${volume === null ? "—" : `${compactVolume(volume)}${unit}`}`,
        ]
        for (const window of windows) {
          const value = asNumber(bar[MA_FIELD[window]])
          if (value !== null) {
            lines.push(`<span style="color:${MA_COLOR[window]}">MA${window}</span> ${priceText(value)}`)
          }
        }
        return lines.join("<br/>")
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 60, right: 16, top: 34, bottom: 132 },
      { left: 60, right: 16, height: 64, bottom: 40 },
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLabel: { hideOverlap: true } },
      { type: "category", gridIndex: 1, data: dates, axisLabel: { show: false } },
    ],
    yAxis: [
      { type: "value", scale: true, splitNumber: 4 },
      {
        type: "value",
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { formatter: (value: number) => (value === 0 ? "0" : compactVolume(value)) },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 60, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 18, start: 60, end: 100 },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: { color: RISE, color0: FALL, borderColor: RISE, borderColor0: FALL },
      },
      ...maSeries,
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barMaxWidth: 10,
      },
    ],
  }
})

async function query(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  bars.value = []
  emptyMessage.value = null
  instrument.value = null

  const wanted = symbol.value.trim().toUpperCase()
  if (!wanted) {
    phase.value = "error"
    failure.value = "请输入证券代码，例如 600519.SH"
    return
  }

  try {
    const found = await searchInstruments(wanted, "CN", 10)
    const hit =
      found.results.find((row) => row.displaySymbol.toUpperCase() === wanted) ??
      found.results.find((row) => row.canonicalCode.toUpperCase() === wanted) ??
      found.results[0] ??
      null
    if (!hit) {
      phase.value = "error"
      failure.value = found.emptyMessage ?? "暂无该证券，请检查代码"
      return
    }
    instrument.value = hit

    const params = new URLSearchParams({ adjustment: adjustment.value, timeframe: "1d" })
    const [start, end] = range.value ?? []
    if (start) {
      params.set("start", start)
    }
    if (end) {
      params.set("end", end)
    }
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const response = await fetch(apiUrl(`/api/v1/market/bars/${hit.id}?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    bars.value = Array.isArray(data?.bars) ? data.bars.flatMap(parseBar) : []
    emptyMessage.value = asString(data?.empty_message)
    phase.value = "ready"
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      return
    }
    failure.value = caught instanceof ApiError ? caught.message : "无法连接行情服务"
    phase.value = "error"
  }
}

function parseBar(value: unknown): DailyBar[] {
  const record = asRecord(value)
  const tradeDate = asString(record?.trade_date)
  const open = asString(record?.open)
  const high = asString(record?.high)
  const low = asString(record?.low)
  const close = asString(record?.close)
  const volume = asString(record?.volume)
  const barCurrency = asString(record?.currency)
  const unit = asString(record?.volume_unit)
  if (!tradeDate || !open || !high || !low || !close || !volume || !barCurrency || !unit) {
    return []
  }
  return [
    {
      trade_date: tradeDate,
      open,
      high,
      low,
      close,
      volume,
      currency: barCurrency,
      volume_unit: unit,
      ma5: asString(record?.ma5),
      ma10: asString(record?.ma10),
      ma20: asString(record?.ma20),
      ma60: asString(record?.ma60),
      simple_return: asString(record?.simple_return),
    },
  ]
}

/** 换代码走路由，URL 与页面内容保持一致；同一代码则按新的筛选条件重查。 */
function openSymbol(next: string): void {
  const code = next.trim().toUpperCase()
  if (!code) {
    return
  }
  if (code === symbol.value) {
    void query()
    return
  }
  void router.push(`/market/stocks/${encodeURIComponent(code)}`)
}

function submit(): void {
  openSymbol(codeInput.value)
}

watch(
  () => props.symbol,
  (next) => {
    const code = (next ?? "").toUpperCase()
    if (code && code !== symbol.value) {
      symbol.value = code
      codeInput.value = code
    }
  },
)

watch(symbol, () => {
  void query()
}, { immediate: true })
</script>

<template>
  <main class="page">
    <PageHeader :title="heading" :subtitle="subtitle">
      <template #tabs>
        <SectionTabs
          :tabs="INDEX_TABS.map((tab) => ({ key: tab.key, label: tab.label }))"
          :model-value="activeIndexTab"
          @update:model-value="openSymbol"
        />
      </template>
      <template #filters>
        <form class="filters-form" @submit.prevent="submit">
          <el-input v-model="codeInput" name="bar-symbol" placeholder="代码，如 600519.SH" style="width: 150px" />
          <el-select v-model="adjustment" name="bar-adjustment" style="width: 100px">
            <el-option label="不复权" value="none" />
            <el-option label="前复权" value="qfq" />
            <el-option label="后复权" value="hfq" />
          </el-select>
          <el-date-picker
            v-model="range"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="~"
            start-placeholder="开始"
            end-placeholder="结束"
            style="width: 230px"
          />
          <el-date-picker
            v-model="asOf"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="观察日"
            style="width: 140px"
          />
          <el-select v-model="activeMas" name="ma-selector" multiple collapse-tags placeholder="均线" style="width: 150px">
            <el-option v-for="window in MA_WINDOWS" :key="window" :label="`MA${window}`" :value="window" />
          </el-select>
          <el-button native-type="submit" type="primary" :loading="phase === 'loading'">查看</el-button>
        </form>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading' || phase === 'idle'" state="loading" :rows="8" />

    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="query" />

    <template v-else>
      <div class="grid-2-1">
        <PanelCard title="日K线" :info="`${adjustmentLabel(adjustment)} · 成交量单位：${volumeUnit || '—'} · 缺失交易日不补`">
          <template #icon><el-icon><TrendCharts /></el-icon></template>
          <template #actions>
            <StatusPill text="日K" tone="accent" />
            <StatusPill :text="adjustmentLabel(adjustment)" tone="muted" />
          </template>
          <div v-if="latest" class="quote-line">
            <span class="close tabular" :class="change ? (change.delta >= 0 ? 'rise' : 'fall') : ''">
              {{ rawPrice(latest.close) }}
            </span>
            <DeltaValue :value="change?.delta ?? null" size="lg" />
            <DeltaValue :value="change?.percent ?? null" suffix="%" size="lg" />
            <span class="quote-date">{{ latest.trade_date }} 收盘 · {{ currency }}</span>
          </div>
          <VChart
            v-if="bars.length"
            class="kline-chart"
            :option="chartOption"
            autoresize
            aria-label="日K线与成交量"
          />
          <StateBlock
            v-else
            state="empty"
            :message="emptyMessage ?? '所选区间内暂无日线，可放宽日期范围或清空观察日后重试'"
          />
        </PanelCard>

        <div class="stack">
          <PanelCard title="当日读数" info="最新一根已入库日线">
            <template #icon><el-icon><Histogram /></el-icon></template>
            <p v-if="!latest" class="muted">暂无日线，无法给出开高低收。</p>
            <template v-else>
              <dl class="ohlc">
                <div v-for="item in ohlc" :key="item.label">
                  <dt>{{ item.label }}</dt>
                  <dd class="tabular">{{ item.value }}</dd>
                </div>
              </dl>
              <p class="volume-line">
                <span class="muted">成交量</span>
                <strong class="tabular">{{ formatAmount(asNumber(latest.volume), volumeUnit) }}</strong>
              </p>
            </template>
          </PanelCard>

          <PanelCard title="均线" info="收盘价相对各条均线的偏离；样本不足时显示「—」">
            <ul class="ma-list">
              <li v-for="item in maReadings" :key="item.window">
                <span class="ma-label"><i :style="{ background: item.color }" />MA{{ item.window }}</span>
                <span class="ma-value tabular">{{ priceText(item.value) }}</span>
                <span class="ma-gap">
                  <DeltaValue v-if="item.gap !== null" :value="item.gap" suffix="%" size="sm" />
                  <span v-else class="muted">样本不足</span>
                </span>
              </li>
            </ul>
          </PanelCard>

          <PanelCard title="区间表现" :info="rangeStats ? `${rangeStats.from} ~ ${rangeStats.to}` : ''">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <p v-if="!rangeStats" class="muted">区间内暂无日线。</p>
            <template v-else>
              <div class="range-head">
                <span class="muted">区间涨跌幅</span>
                <DeltaValue :value="rangeStats.percent" suffix="%" size="lg" />
              </div>
              <dl class="kv-grid">
                <div>
                  <dt>区间最高</dt>
                  <dd class="tabular">{{ priceText(rangeStats.high?.value ?? null) }}</dd>
                  <dd class="sub">{{ rangeStats.high?.date ?? "—" }}</dd>
                </div>
                <div>
                  <dt>区间最低</dt>
                  <dd class="tabular">{{ priceText(rangeStats.low?.value ?? null) }}</dd>
                  <dd class="sub">{{ rangeStats.low?.date ?? "—" }}</dd>
                </div>
                <div>
                  <dt>日均成交量</dt>
                  <dd class="tabular">{{ formatAmount(rangeStats.avgVolume, volumeUnit) }}</dd>
                </div>
                <div>
                  <dt>交易日数</dt>
                  <dd class="tabular">{{ rangeStats.days }}<small> 日</small></dd>
                </div>
              </dl>
            </template>
          </PanelCard>
        </div>
      </div>

      <PanelCard class="bars-card" title="日线明细" :info="`共 ${bars.length} 个交易日，最新在上`" flush>
        <el-table :data="tableRows" size="small" row-key="trade_date" max-height="380">
          <el-table-column prop="trade_date" label="交易日" width="120" fixed />
          <el-table-column label="开盘" min-width="96" align="right">
            <template #default="{ row }"><span class="tabular">{{ rawPrice(row.open) }}</span></template>
          </el-table-column>
          <el-table-column label="最高" min-width="96" align="right">
            <template #default="{ row }"><span class="tabular">{{ rawPrice(row.high) }}</span></template>
          </el-table-column>
          <el-table-column label="最低" min-width="96" align="right">
            <template #default="{ row }"><span class="tabular">{{ rawPrice(row.low) }}</span></template>
          </el-table-column>
          <el-table-column label="收盘" min-width="96" align="right">
            <template #default="{ row }"><span class="tabular strong">{{ rawPrice(row.close) }}</span></template>
          </el-table-column>
          <el-table-column label="涨跌幅" min-width="96" align="right">
            <template #default="{ row }"><DeltaValue :value="returnPercent(row)" suffix="%" /></template>
          </el-table-column>
          <el-table-column :label="`成交量（${volumeUnit || '—'}）`" min-width="120" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ formatAmount(asNumber(row.volume)) }}</span>
            </template>
          </el-table-column>
          <el-table-column v-for="window in MA_WINDOWS" :key="window" :label="`MA${window}`" min-width="96" align="right">
            <template #default="{ row }">
              <span class="tabular muted-num">{{ rawPrice(row[MA_FIELD[window]]) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <SourceFooter
        :as-of="latest ? `${latest.trade_date} 收盘` : '—'"
        :sources="['A 股日线（已入库）']"
        :note="`${adjustmentLabel(adjustment)} · 缺失交易日不补价格，观察日之后的 K 线不显示`"
      />
    </template>
  </main>
</template>

<style scoped>
.filters-form {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.quote-line {
  display: flex;
  align-items: baseline;
  gap: 14px;
  flex-wrap: wrap;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
}

.quote-line .close {
  font-family: var(--font-num);
  font-size: 30px;
  font-weight: 700;
  line-height: 1;
  color: var(--text-strong);
}

.quote-line .close.rise {
  color: var(--rise);
}

.quote-line .close.fall {
  color: var(--fall);
}

.quote-date {
  font-size: 13px;
  color: var(--muted);
}

.kline-chart {
  width: 100%;
  height: 500px;
  margin-top: 10px;
}

.ohlc {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}

.ohlc dt,
.kv-grid dt {
  font-size: 13px;
  color: var(--muted);
}

.ohlc dd,
.kv-grid dd {
  margin: 2px 0 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-strong);
}

.kv-grid dd small {
  font-size: 13px;
  font-weight: 400;
  color: var(--muted);
}

.kv-grid dd.sub {
  margin-top: 0;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

.volume-line {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
  font-size: 13px;
}

.volume-line strong {
  font-size: 16px;
  color: var(--text-strong);
}

.ma-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.ma-list li {
  display: grid;
  grid-template-columns: 64px 1fr auto;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
}

.ma-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
}

.ma-label i {
  width: 10px;
  height: 3px;
  border-radius: 2px;
}

.ma-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-strong);
}

.ma-gap {
  text-align: right;
}

.range-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding-bottom: 10px;
  margin-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
  margin: 0;
}

.bars-card {
  margin-top: var(--gap);
}

.strong {
  font-weight: 600;
  color: var(--text-strong);
}

.muted-num {
  color: var(--muted);
}
</style>
