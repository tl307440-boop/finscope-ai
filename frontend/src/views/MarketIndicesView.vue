<script setup lang="ts">
/**
 * 指数行情。
 *
 * 数据库里只入库了 000001.SH 上证指数、000300.SH 沪深300、399001.SZ 深证成指、
 * 399006.SZ 创业板指这四个中国指数，因此本页只列这四个：
 * 先用 /instruments/search 换到 instrument id，再取 /market/bars/{id} 的日线。
 * 没有入库的日期不会补价格，图上与表里只画真实返回的交易日。
 */
import { computed, onMounted, ref } from "vue"
import { DataLine, Histogram, TrendCharts } from "@element-plus/icons-vue"
import { VChart } from "../charts"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { adjustmentLabel, volumeUnitLabel } from "../api/bars"
import type { DailyBar } from "../api/bars"
import { asNumber, asRecord, asString, dataOf, formatAmount, formatMoment, scaleAmount } from "../api/json"
import type { EChartsOption } from "echarts"

type Phase = "loading" | "ready" | "error"

/** 规格书列出的四个已入库指数；顺序即展示顺序。 */
const INDEXES: { symbol: string; fallbackName: string }[] = [
  { symbol: "000001.SH", fallbackName: "上证指数" },
  { symbol: "000300.SH", fallbackName: "沪深300" },
  { symbol: "399001.SZ", fallbackName: "深证成指" },
  { symbol: "399006.SZ", fallbackName: "创业板指" },
]

const DEFAULT_DAYS = 90

/** 与主题 candlestick 默认色一致：红涨绿跌。 */
const RISE = "#f6465d"
const FALL = "#1fc37d"
const MA_LINES = [
  { key: "ma5", label: "MA5", color: "#fbbf24" },
  { key: "ma10", label: "MA10", color: "#60a5fa" },
  { key: "ma20", label: "MA20", color: "#c084fc" },
] as const

interface SeriesState {
  symbol: string
  name: string
  instrumentId: string
  bars: DailyBar[]
  emptyMessage: string | null
  truncated: boolean
  /** 后端返回的复权口径（none / qfq / hfq），页面上要标出来 */
  adjustment: string
  problem: string | null
}

interface MetaInfo {
  asOf: string | null
  source: string | null
  stale: boolean
  dataMode: string | null
  requestId: string | null
}

/** 一个指数在当前区间内的摘要，全部由已返回的日线算出。 */
interface SeriesSummary {
  close: number | null
  tradeDate: string
  change: number | null
  changePercent: number | null
  rangePercent: number | null
  high: { value: number; date: string } | null
  low: { value: number; date: string } | null
  avgVolume: number | null
  days: number
  spark: number[]
}

const phase = ref<Phase>("loading")
const errorMessage = ref("")
const meta = ref<MetaInfo>({
  asOf: null,
  source: null,
  stale: false,
  dataMode: null,
  requestId: null,
})
const range = ref<string[]>(defaultRange())
const activeSymbol = ref(INDEXES[0]?.symbol ?? "000001.SH")
const seriesMap = ref<Record<string, SeriesState>>({})
const loading = ref(false)

function pad(value: number): string {
  return String(value).padStart(2, "0")
}

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function defaultRange(): string[] {
  const end = new Date()
  const start = new Date(end.getTime() - (DEFAULT_DAYS - 1) * 24 * 60 * 60 * 1000)
  return [isoDay(start), isoDay(end)]
}

function priceText(value: number | null): string {
  if (value === null) {
    return "—"
  }
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function rawPrice(value: string | null | undefined): string {
  return priceText(asNumber(value ?? null))
}

function summarize(item: SeriesState | undefined): SeriesSummary {
  const rows = item?.bars ?? []
  const closes = rows.map((bar) => asNumber(bar.close))
  const last = rows[rows.length - 1]
  const close = last ? asNumber(last.close) : null
  const previous = rows.length > 1 ? asNumber(rows[rows.length - 2]?.close ?? null) : null
  const first = closes.find((value): value is number => value !== null) ?? null
  let high: SeriesSummary["high"] = null
  let low: SeriesSummary["low"] = null
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
  const change = close !== null && previous !== null ? close - previous : null
  return {
    close,
    tradeDate: last?.trade_date ?? "",
    change,
    changePercent: change !== null && previous ? (change / previous) * 100 : null,
    rangePercent: close !== null && first ? ((close - first) / first) * 100 : null,
    high,
    low,
    avgVolume: volumeCount ? volumeSum / volumeCount : null,
    days: rows.length,
    spark: closes.filter((value): value is number => value !== null).slice(-60),
  }
}

const summaries = computed<Record<string, SeriesSummary>>(() => {
  const result: Record<string, SeriesSummary> = {}
  for (const item of INDEXES) {
    result[item.symbol] = summarize(seriesMap.value[item.symbol])
  }
  return result
})

const active = computed<SeriesState | null>(() => seriesMap.value[activeSymbol.value] ?? null)
const activeSummary = computed<SeriesSummary>(() => summaries.value[activeSymbol.value] ?? summarize(undefined))
const bars = computed<DailyBar[]>(() => active.value?.bars ?? [])
const latestBar = computed<DailyBar | null>(() => bars.value[bars.value.length - 1] ?? null)
const asOfText = computed(() => formatMoment(meta.value.asOf))
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")
const volumeUnit = computed(() => volumeUnitLabel(latestBar.value?.volume_unit))

/** 指数切换页签，名称取数据库返回的 name_cn。 */
const indexTabs = computed(() =>
  INDEXES.map((item) => ({
    key: item.symbol,
    label: seriesMap.value[item.symbol]?.name ?? item.fallbackName,
  })),
)

/** 收盘相对各条均线的偏离，MA 为空（样本不足）时不算。 */
const maRows = computed(() => {
  const bar = latestBar.value
  const close = bar ? asNumber(bar.close) : null
  return (["ma5", "ma10", "ma20", "ma60"] as const).map((key) => {
    const value = bar ? asNumber(bar[key]) : null
    return {
      key,
      label: key.toUpperCase(),
      value,
      gap: value !== null && close !== null && value !== 0 ? ((close - value) / value) * 100 : null,
    }
  })
})

function sparkTone(symbol: string): "up" | "down" {
  const points = summaries.value[symbol]?.spark ?? []
  const first = points[0]
  const last = points[points.length - 1]
  return first !== undefined && last !== undefined && last < first ? "down" : "up"
}

function cardValue(symbol: string): string | null {
  const close = summaries.value[symbol]?.close ?? null
  return close === null ? null : priceText(close)
}

function cardCaption(symbol: string): string {
  const item = seriesMap.value[symbol]
  if (!item) {
    return symbol
  }
  if (item.problem) {
    return item.problem
  }
  const summary = summaries.value[symbol]
  return summary?.tradeDate ? `${symbol} · ${summary.tradeDate}` : `${symbol} · 区间内暂无日线`
}

function compactVolume(value: number): string {
  const { text, scale } = scaleAmount(value, value >= 1e8 ? 1 : 0)
  return `${text}${scale}`
}

function dataModeText(mode: string | null): string {
  if (mode === null) {
    return "—"
  }
  return mode === "live" ? "正式数据" : mode === "demo" ? "演示数据" : "非正式数据"
}

/** K 线 + 均线 + 成交量：主轴一张图两个 grid，数据只来自已入库日线。 */
const chartOption = computed<EChartsOption>(() => {
  const rows = bars.value
  const dates = rows.map((bar) => bar.trade_date)
  const candles = rows.map((bar) => [
    asNumber(bar.open),
    asNumber(bar.close),
    asNumber(bar.low),
    asNumber(bar.high),
  ])
  const volumes = rows.map((bar) => {
    const rising = (asNumber(bar.close) ?? 0) >= (asNumber(bar.open) ?? 0)
    return {
      value: asNumber(bar.volume),
      itemStyle: { color: rising ? "rgba(246,70,93,0.62)" : "rgba(31,195,125,0.62)" },
    }
  })
  const unit = volumeUnit.value
  return {
    animation: false,
    legend: { top: 0, left: 0, data: ["K线", ...MA_LINES.map((line) => line.label)] },
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
        const lines = [
          `<b>${bar.trade_date}</b>`,
          `开 ${rawPrice(bar.open)}　高 ${rawPrice(bar.high)}`,
          `低 ${rawPrice(bar.low)}　收 ${rawPrice(bar.close)}`,
          `成交量 ${volume === null ? "—" : `${compactVolume(volume)}${unit}`}`,
        ]
        for (const line of MA_LINES) {
          const value = asNumber(bar[line.key])
          if (value !== null) {
            lines.push(`<span style="color:${line.color}">${line.label}</span> ${priceText(value)}`)
          }
        }
        return lines.join("<br/>")
      },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 58, right: 16, top: 34, bottom: 132 },
      { left: 58, right: 16, height: 64, bottom: 40 },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLabel: { hideOverlap: true },
      },
      {
        type: "category",
        gridIndex: 1,
        data: dates,
        axisLabel: { show: false },
      },
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
      { type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 6, height: 18 },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: { color: RISE, color0: FALL, borderColor: RISE, borderColor0: FALL },
      },
      ...MA_LINES.map((line) => ({
        name: line.label,
        type: "line" as const,
        data: rows.map((bar) => asNumber(bar[line.key])),
        smooth: true,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: line.color, width: 1.2 },
        itemStyle: { color: line.color },
      })),
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

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError("无法连接行情服务", "NETWORK_ERROR", null)
}

function readMeta(body: unknown): MetaInfo {
  const record = asRecord(asRecord(body)?.meta)
  const stale = record?.stale
  return {
    asOf: asString(record?.as_of),
    source: asString(record?.source),
    stale: typeof stale === "boolean" ? stale : false,
    dataMode: asString(record?.data_mode),
    requestId: asString(record?.request_id),
  }
}

function toBar(record: Record<string, unknown>): DailyBar {
  return {
    trade_date: asString(record.trade_date) ?? "—",
    open: asString(record.open) ?? "—",
    high: asString(record.high) ?? "—",
    low: asString(record.low) ?? "—",
    close: asString(record.close) ?? "—",
    volume: asString(record.volume) ?? "—",
    currency: asString(record.currency) ?? "",
    volume_unit: asString(record.volume_unit) ?? "",
    ma5: asString(record.ma5),
    ma10: asString(record.ma10),
    ma20: asString(record.ma20),
    ma60: asString(record.ma60),
    simple_return: asString(record.simple_return),
  }
}

async function getJson(path: string): Promise<{ data: Record<string, unknown> | null; meta: MetaInfo }> {
  const response = await fetch(apiUrl(path), {
    headers: { Accept: "application/json" },
    credentials: "include",
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return { data: dataOf(body), meta: readMeta(body) }
}

async function loadOne(
  item: { symbol: string; fallbackName: string },
  rangeQuery: string,
): Promise<{ state: SeriesState; meta: MetaInfo | null; failure: ApiError | null }> {
  const symbol = item.symbol
  try {
    const found = await getJson(`/api/v1/instruments/search?q=${symbol.slice(0, 6)}&market=CN`)
    const hit = (Array.isArray(found.data?.results) ? found.data.results : [])
      .flatMap((row) => {
        const record = asRecord(row)
        return record ? [record] : []
      })
      .find((record) => asString(record.display_symbol) === symbol)
    const instrumentId = asString(hit?.id)
    if (!hit || !instrumentId) {
      return {
        state: {
          symbol,
          name: item.fallbackName,
          instrumentId: "",
          bars: [],
          emptyMessage: "该指数尚未入库",
          truncated: false,
          adjustment: "none",
          problem: "该指数尚未入库",
        },
        meta: found.meta,
        failure: null,
      }
    }
    const page = await getJson(`/api/v1/market/bars/${instrumentId}${rangeQuery}`)
    const rows = (Array.isArray(page.data?.bars) ? page.data.bars : []).flatMap((row) => {
      const record = asRecord(row)
      return record ? [toBar(record)] : []
    })
    return {
      state: {
        symbol,
        name: asString(page.data?.name_cn) ?? asString(hit.name_cn) ?? item.fallbackName,
        instrumentId,
        bars: rows,
        emptyMessage: asString(page.data?.empty_message),
        truncated: page.data?.truncated === true,
        adjustment: asString(page.data?.adjustment) ?? "none",
        problem: null,
      },
      meta: page.meta,
      failure: null,
    }
  } catch (error) {
    const apiError = toApiError(error)
    return {
      state: {
        symbol,
        name: item.fallbackName,
        instrumentId: "",
        bars: [],
        emptyMessage: null,
        truncated: false,
        adjustment: "none",
        problem: apiError.message,
      },
      meta: null,
      failure: apiError,
    }
  }
}

async function load(): Promise<void> {
  loading.value = true
  phase.value = "loading"
  errorMessage.value = ""

  const start = range.value[0] ?? ""
  const end = range.value[1] ?? ""
  const params = new URLSearchParams()
  if (start) {
    params.set("start", start)
  }
  if (end) {
    params.set("end", end)
  }
  const rangeQuery = params.size ? `?${params.toString()}` : ""

  try {
    const results = await Promise.all(INDEXES.map((item) => loadOne(item, rangeQuery)))
    const next: Record<string, SeriesState> = {}
    for (const result of results) {
      next[result.state.symbol] = result.state
    }
    seriesMap.value = next
    const firstMeta = results.find((result) => result.meta)?.meta ?? null
    if (firstMeta) {
      meta.value = firstMeta
    }
    // 四个指数全部失败才算整页错误；部分失败会在对应指数上标注原因。
    const failure = results.find((result) => result.failure)?.failure ?? null
    if (results.every((result) => result.state.problem !== null) && failure) {
      errorMessage.value = failure.message
      phase.value = "error"
      return
    }
    phase.value = "ready"
  } finally {
    loading.value = false
  }
}

function changedSymbol(symbol: string): void {
  activeSymbol.value = symbol
}

function resetRange(): void {
  range.value = defaultRange()
  void load()
}

function pickRow(row: { symbol: string }): void {
  changedSymbol(row.symbol)
}

function rowClass({ row }: { row: { symbol: string } }): string {
  return row.symbol === activeSymbol.value ? "is-active-row" : ""
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <PageHeader title="市场行情" subtitle="A 股主要指数日线 · 缺失交易日不补价格">
      <template #actions>
        <span class="filter-label">时间范围</span>
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="~"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          :clearable="false"
          style="width: 260px"
          @change="load"
        />
        <el-button text @click="resetRange">近 {{ DEFAULT_DAYS }} 天</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'error'" state="error" :message="errorMessage" @retry="load" />

    <template v-else>
      <div class="grid-4">
        <StatCard
          v-for="item in INDEXES"
          :key="item.symbol"
          class="index-card"
          :class="{ selected: item.symbol === activeSymbol }"
          :title="seriesMap[item.symbol]?.name ?? item.fallbackName"
          :value="cardValue(item.symbol)"
          :caption="phase === 'loading' ? '加载中…' : cardCaption(item.symbol)"
          role="button"
          tabindex="0"
          @click="changedSymbol(item.symbol)"
          @keydown.enter="changedSymbol(item.symbol)"
        >
          <template #icon><el-icon><Histogram /></el-icon></template>
          <template #below>
            <p v-if="(summaries[item.symbol]?.change ?? null) !== null" class="card-delta">
              <DeltaValue :value="summaries[item.symbol]?.change ?? null" size="md" />
              <DeltaValue :value="summaries[item.symbol]?.changePercent ?? null" suffix="%" size="md" />
            </p>
          </template>
          <template #chart>
            <MiniSpark
              v-if="(summaries[item.symbol]?.spark.length ?? 0) > 1"
              :points="summaries[item.symbol]?.spark ?? []"
              kind="area"
              :tone="sparkTone(item.symbol)"
              :width="120"
              :height="44"
            />
          </template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <PanelCard title="指数走势" info="日 K 线、均线与成交量；十字线同时对齐价格与成交量">
          <template #icon><el-icon><TrendCharts /></el-icon></template>
          <template #actions>
            <SectionTabs :tabs="indexTabs" :model-value="activeSymbol" @update:model-value="changedSymbol" />
          </template>

          <StateBlock v-if="loading" state="loading" :rows="8" />
          <StateBlock
            v-else-if="bars.length === 0"
            state="empty"
            :message="active?.problem ?? active?.emptyMessage ?? '所选区间内暂无该指数的日线，可放宽时间范围后重试'"
          />
          <template v-else>
            <div class="ohlc-row">
              <span class="ohlc-date tabular">{{ latestBar?.trade_date ?? "—" }}</span>
              <span class="ohlc-item"><em>开</em><strong>{{ rawPrice(latestBar?.open) }}</strong></span>
              <span class="ohlc-item"><em>高</em><strong>{{ rawPrice(latestBar?.high) }}</strong></span>
              <span class="ohlc-item"><em>低</em><strong>{{ rawPrice(latestBar?.low) }}</strong></span>
              <span class="ohlc-item">
                <em>收</em>
                <strong>{{ rawPrice(latestBar?.close) }}</strong>
                <DeltaValue :value="activeSummary.changePercent" suffix="%" size="sm" />
              </span>
              <span class="ohlc-item">
                <em>成交量</em>
                <strong>{{ formatAmount(asNumber(latestBar?.volume ?? null), volumeUnit) }}</strong>
              </span>
              <span class="ohlc-item">
                <StatusPill :text="adjustmentLabel(active?.adjustment ?? 'none')" tone="muted" />
              </span>
            </div>
            <VChart class="chart-main" :option="chartOption" autoresize />
          </template>
        </PanelCard>

        <div class="stack">
          <PanelCard title="区间表现" :info="`${range[0] ?? '—'} ~ ${range[1] ?? '—'}，按区间首尾收盘计算`">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <StateBlock v-if="loading" state="loading" :rows="4" />
            <StateBlock v-else-if="!activeSummary.days" state="empty" message="区间内暂无日线" />
            <template v-else>
              <div class="range-head">
                <span class="muted">区间涨跌幅</span>
                <DeltaValue :value="activeSummary.rangePercent" suffix="%" size="lg" />
              </div>
              <dl class="kv-grid">
                <div>
                  <dt>区间最高</dt>
                  <dd class="tabular">{{ priceText(activeSummary.high?.value ?? null) }}</dd>
                  <dd class="sub">{{ activeSummary.high?.date ?? "—" }}</dd>
                </div>
                <div>
                  <dt>区间最低</dt>
                  <dd class="tabular">{{ priceText(activeSummary.low?.value ?? null) }}</dd>
                  <dd class="sub">{{ activeSummary.low?.date ?? "—" }}</dd>
                </div>
                <div>
                  <dt>日均成交量</dt>
                  <dd class="tabular">{{ formatAmount(activeSummary.avgVolume, volumeUnit) }}</dd>
                </div>
                <div>
                  <dt>交易日数</dt>
                  <dd class="tabular">{{ activeSummary.days }}<small> 日</small></dd>
                </div>
              </dl>
            </template>
          </PanelCard>

          <PanelCard title="均线位置" info="收盘价相对各条均线的偏离；均线样本不足时显示「—」">
            <ul class="ma-list">
              <li v-for="row in maRows" :key="row.key">
                <span class="ma-label">{{ row.label }}</span>
                <span class="ma-value tabular">{{ priceText(row.value) }}</span>
                <span class="ma-gap">
                  <DeltaValue v-if="row.gap !== null" :value="row.gap" suffix="%" size="sm" />
                  <span v-else class="muted">样本不足</span>
                </span>
              </li>
            </ul>
          </PanelCard>

          <PanelCard title="数据口径">
            <ul class="plain-list">
              <li>
                <span>复权方式</span>
                <strong>{{ adjustmentLabel(active?.adjustment ?? "none") }}</strong>
              </li>
              <li>
                <span>区间完整性</span>
                <StatusPill
                  :text="active?.truncated ? '条数超限，仅显示部分' : '完整'"
                  :tone="active?.truncated ? 'warn' : 'ok'"
                />
              </li>
              <li>
                <span>数据类型</span>
                <StatusPill :text="dataModeText(meta.dataMode)" :tone="demoMode ? 'warn' : 'ok'" />
              </li>
            </ul>
          </PanelCard>
        </div>
      </div>

      <PanelCard title="主要指数概览" info="点击行切换上方走势图" class="section" flush>
        <el-table
          :data="INDEXES"
          size="small"
          :row-class-name="rowClass"
          class="clickable-table"
          @row-click="pickRow"
        >
          <el-table-column label="指数" min-width="150">
            <template #default="{ row }">
              <span class="cell-name">{{ seriesMap[row.symbol]?.name ?? row.fallbackName }}</span>
              <span class="cell-code tabular">{{ row.symbol }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最新交易日" width="120">
            <template #default="{ row }">
              <span class="tabular">{{ summaries[row.symbol]?.tradeDate || "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="收盘" width="120" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ priceText(summaries[row.symbol]?.close ?? null) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="日涨跌幅" width="110" align="right">
            <template #default="{ row }">
              <DeltaValue :value="summaries[row.symbol]?.changePercent ?? null" suffix="%" />
            </template>
          </el-table-column>
          <el-table-column label="区间涨跌幅" width="120" align="right">
            <template #default="{ row }">
              <DeltaValue :value="summaries[row.symbol]?.rangePercent ?? null" suffix="%" />
            </template>
          </el-table-column>
          <el-table-column label="区间最高" width="120" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ priceText(summaries[row.symbol]?.high?.value ?? null) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="区间最低" width="120" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ priceText(summaries[row.symbol]?.low?.value ?? null) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="区间走势" width="140" align="center">
            <template #default="{ row }">
              <MiniSpark
                v-if="(summaries[row.symbol]?.spark.length ?? 0) > 1"
                class="table-spark"
                :points="summaries[row.symbol]?.spark ?? []"
                :tone="sparkTone(row.symbol)"
                :width="110"
                :height="26"
              />
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="110">
            <template #default="{ row }">
              <StatusPill
                :text="seriesMap[row.symbol]?.problem ? '暂无数据' : '已入库'"
                :tone="seriesMap[row.symbol]?.problem ? 'warn' : 'ok'"
                :title="seriesMap[row.symbol]?.problem ?? ''"
                dot
              />
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <SourceFooter
        :as-of="asOfText"
        :sources="['A 股指数日线（已入库）']"
        :stale="meta.stale"
        :note="demoMode ? `当前为${dataModeText(meta.dataMode)}` : '缺失交易日不补价格，页面不承诺实时'"
      />
    </template>
  </div>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.filter-label {
  font-size: 13px;
  color: var(--muted);
}

.index-card {
  cursor: pointer;
  transition: border-color 160ms ease;
}

.index-card:hover,
.index-card.selected {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--line));
}

.card-delta {
  display: flex;
  gap: 10px;
  margin: 4px 0 0;
}

.chart-main {
  width: 100%;
  height: 480px;
  margin-top: 10px;
}

.ohlc-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 20px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
}

.ohlc-date {
  font-size: 13px;
  color: var(--muted);
}

.ohlc-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  font-size: 14px;
}

.ohlc-item em {
  font-style: normal;
  font-size: 13px;
  color: var(--muted);
}

.ohlc-item strong {
  font-family: var(--font-num);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-strong);
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

.kv-grid dt {
  font-size: 13px;
  color: var(--muted);
}

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

.ma-list,
.plain-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.ma-list li {
  display: grid;
  grid-template-columns: 56px 1fr auto;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
}

.ma-label {
  color: var(--muted);
}

.ma-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-strong);
}

.ma-gap {
  text-align: right;
}

.plain-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  color: var(--muted);
}

.plain-list strong {
  color: var(--text);
  font-weight: 500;
}

.cell-name {
  font-weight: 600;
  color: var(--text-strong);
}

.cell-code {
  margin-left: 8px;
  font-size: 12px;
  color: var(--muted);
}

.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}

.clickable-table :deep(.is-active-row td.el-table__cell) {
  background: var(--accent-soft);
}

.table-spark {
  display: inline-block;
}
</style>
