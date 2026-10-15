<script setup lang="ts">
/**
 * 个股 / 指数行情（规格书 §1.3 `/market/stocks/:symbol`）。
 *
 * 组成：StockHeader（代码、名称、最新收盘、涨跌）+ K 线（Apache ECharts）
 * + MASelector（MA5/10/20/60）+ 成交量。缺失交易日不补 0，观察日之后的 K 线不显示。
 * 涨跌色按 A 股习惯红涨绿跌，并同时用 +/- 与箭头标注，兼顾色盲模式。
 */
import { computed, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { adjustmentLabel, maLabel, returnLabel, summarizeBars, type DailyBar } from "../api/bars"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"

const props = defineProps<{ symbol?: string }>()

const MA_WINDOWS = [5, 10, 20, 60] as const
type MaWindow = (typeof MA_WINDOWS)[number]
const MA_FIELD: Record<MaWindow, keyof DailyBar> = {
  5: "ma5",
  10: "ma10",
  20: "ma20",
  60: "ma60",
}
const MA_COLOR: Record<MaWindow, string> = {
  5: "#f59e0b",
  10: "#3b82f6",
  20: "#a855f7",
  60: "#14b8a6",
}
const RISE = "#f87171"
const FALL = "#34d399"

const symbol = ref(props.symbol ?? "600519.SH")
const activeMas = ref<MaWindow[]>([5, 10, 20])
const adjustment = ref("none")
const start = ref("")
const end = ref("")
const asOf = ref("")

const instrument = ref<InstrumentHit | null>(null)
const bars = ref<DailyBar[]>([])
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")
const lines = ref<string[]>([])

const heading = computed(() => {
  const item = instrument.value
  if (!item) {
    return symbol.value
  }
  const name = item.nameCn || item.nameEn || item.canonicalCode
  return `${item.canonicalCode} ${name}`
})

/** 最新一根与上一根的收盘价，用来算涨跌。 */
const latest = computed<DailyBar | null>(() => bars.value.at(-1) ?? null)
const previous = computed<DailyBar | null>(() => bars.value.at(-2) ?? null)

const change = computed(() => {
  const now = latest.value
  const before = previous.value
  if (!now || !before) {
    return null
  }
  const close = Number(now.close)
  const prior = Number(before.close)
  if (!Number.isFinite(close) || !Number.isFinite(prior) || prior === 0) {
    return null
  }
  const delta = close - prior
  return { delta, percent: (delta / prior) * 100, rising: delta >= 0 }
})

const chartOption = computed<EChartsOption>(() => {
  const rows = bars.value
  const dates = rows.map((row) => row.trade_date)
  const candles = rows.map((row) => [
    Number(row.open),
    Number(row.close),
    Number(row.low),
    Number(row.high),
  ])
  const volumes = rows.map((row) => ({
    value: Number(row.volume),
    itemStyle: {
      color: Number(row.close) >= Number(row.open) ? "rgba(248,113,113,0.55)" : "rgba(52,211,153,0.55)",
    },
  }))

  const maSeries = activeMas.value.map((window) => ({
    name: `MA${window}`,
    type: "line" as const,
    data: rows.map((row) => {
      const raw = row[MA_FIELD[window]]
      const value = raw === null || raw === undefined ? Number.NaN : Number(raw)
      return Number.isFinite(value) ? value : null
    }),
    smooth: true,
    showSymbol: false,
    lineStyle: { width: 1.2, color: MA_COLOR[window] },
    itemStyle: { color: MA_COLOR[window] },
    connectNulls: false,
  }))

  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: {
      data: ["K线", ...activeMas.value.map((window) => `MA${window}`)],
      textStyle: { color: "#95a6be" },
      top: 0,
    },
    grid: [
      { left: 56, right: 24, top: 32, height: "54%" },
      { left: 56, right: 24, top: "72%", height: "16%" },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: "#95a6be" } },
        axisLabel: { color: "#95a6be" },
      },
      {
        type: "category",
        gridIndex: 1,
        data: dates,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: "#95a6be" } },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: "rgba(231,237,248,0.12)" } },
        axisLabel: { color: "#95a6be" },
      },
      {
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { color: "#95a6be" },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 60, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 18, start: 60, end: 100 },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: RISE,
          color0: FALL,
          borderColor: RISE,
          borderColor0: FALL,
        },
      },
      ...maSeries,
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
      },
    ],
  }
})

const volumeUnit = computed(() => latest.value?.volume_unit ?? "")
const currency = computed(() => instrument.value?.currency ?? "CNY")

async function query(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  lines.value = []
  bars.value = []
  instrument.value = null

  const wanted = symbol.value.trim().toUpperCase()
  if (!wanted) {
    phase.value = "error"
    failure.value = "请输入证券代码"
    return
  }

  try {
    const found = await searchInstruments(wanted, "CN", 10)
    const hit =
      found.results.find((row) => row.canonicalCode.toUpperCase() === wanted) ??
      found.results[0] ??
      null
    if (!hit) {
      phase.value = "error"
      failure.value = found.emptyMessage ?? "暂无该证券"
      return
    }
    instrument.value = hit

    const params = new URLSearchParams({ adjustment: adjustment.value, timeframe: "1d" })
    if (start.value) {
      params.set("start", start.value)
    }
    if (end.value) {
      params.set("end", end.value)
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
    const rows = Array.isArray(data?.bars) ? data.bars.flatMap(parseBar) : []
    bars.value = rows
    lines.value = summarizeBars({ emptyMessage: asString(data?.empty_message), bars: rows })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
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

watch(
  () => props.symbol,
  (next) => {
    if (next && next !== symbol.value) {
      symbol.value = next
    }
  },
)

watch(symbol, () => {
  void query()
}, { immediate: true })
</script>

<template>
  <main class="page">
    <section class="panel">
      <h1>市场行情</h1>
      <p class="muted">
        个股与指数都从已入库日线读取。缺失交易日不补 0，观察日之后的 K 线不显示；页面不承诺实时。
      </p>

      <form class="form" @submit.prevent="query">
        <label>
          证券代码
          <input v-model="symbol" name="bar-symbol" placeholder="600519.SH" />
        </label>
        <label>
          复权
          <select v-model="adjustment" name="bar-adjustment">
            <option value="none">不复权</option>
            <option value="qfq">前复权</option>
            <option value="hfq">后复权</option>
          </select>
        </label>
        <label>
          开始日期
          <input v-model="start" name="bar-start" type="date" />
        </label>
        <label>
          结束日期
          <input v-model="end" name="bar-end" type="date" />
        </label>
        <label>
          观察日
          <input v-model="asOf" name="bar-as-of" type="date" />
        </label>
        <label>
          均线
          <el-select v-model="activeMas" name="ma-selector" multiple collapse-tags>
            <el-option v-for="window in MA_WINDOWS" :key="window" :label="`MA${window}`" :value="window" />
          </el-select>
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">查看日线</button>
      </form>

      <p v-if="phase === 'loading'" class="skeleton">正在查询</p>
      <p v-else-if="phase === 'error'" class="alert" role="alert">{{ failure }}</p>
      <template v-else-if="phase === 'ready'">
        <header class="stock-header">
          <h2>{{ heading }}</h2>
          <p v-if="latest" class="quote">
            <span class="close">{{ latest.close }} {{ currency }}</span>
            <span v-if="change" :class="change.rising ? 'rise' : 'fall'">
              {{ change.rising ? "▲" : "▼" }}
              {{ change.rising ? "+" : "" }}{{ change.delta.toFixed(2) }}
              （{{ change.rising ? "+" : "" }}{{ change.percent.toFixed(2) }}%）
            </span>
            <span class="muted">{{ latest.trade_date }} 收盘</span>
          </p>
          <p class="legend">
            <span class="rise">涨 +</span>
            <span class="fall">跌 −</span>
            {{ adjustmentLabel(adjustment) }} · 成交量单位 {{ volumeUnit || "—" }} · 不补缺失交易日
          </p>
        </header>

        <VChart
          v-if="bars.length"
          class="kline-chart"
          :option="chartOption"
          autoresize
          aria-label="日K线与成交量"
        />
        <p v-else class="muted">这段时间没有入库日线。</p>

        <ul v-if="latest" class="provider-list">
          <li>{{ maLabel(5, latest.ma5) }}</li>
          <li>{{ maLabel(10, latest.ma10) }}</li>
          <li>{{ maLabel(20, latest.ma20) }}</li>
          <li>{{ maLabel(60, latest.ma60) }}</li>
          <li>{{ returnLabel(latest.simple_return) }}</li>
        </ul>
        <ul class="provider-list">
          <li v-for="line in lines" :key="line">{{ line }}</li>
        </ul>
      </template>
    </section>
  </main>
</template>

<style scoped>
.kline-chart {
  width: 100%;
  height: 460px;
  margin-top: 12px;
}

.stock-header h2 {
  margin: 12px 0 4px;
}

.quote {
  display: flex;
  gap: 12px;
  align-items: baseline;
  margin: 0;
}

.quote .close {
  font-size: 20px;
  font-weight: 600;
}
</style>
