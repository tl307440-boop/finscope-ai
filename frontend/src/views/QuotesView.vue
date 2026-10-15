<script setup lang="ts">
/**
 * 个股 / 指数行情（规格书 §1.3 `/market/stocks/:symbol`）。
 *
 * 组成：PageHeader（代码 + 名称 + 最新收盘 + 涨跌）+ 分时/日K/周K/月K 周期切换
 * + K 线（ECharts 蜡烛图 + 成交量副图）+ 当日 OHLC / 均线读数。
 * 缺失交易日不补 0，观察日之后的 K 线不显示。涨跌色按 A 股习惯红涨绿跌
 * （统一走 DeltaValue），并同时用 +/- 标注，兼顾色盲模式。
 */
import { computed, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { adjustmentLabel, maLabel, summarizeBars, type DailyBar } from "../api/bars"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import DeltaValue from "../components/ui/DeltaValue.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"

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

/**
 * 效果图中间的周期切换条。后端 `/market/bars/{id}` 的 timeframe 只接受 `1d`
 * （`Literal["1d"]`），所以分时 / 周K / 月K 只做展示、不发请求，点它会写明原因，
 * 不用日线拼出周月线来冒充。
 */
const PERIODS = [
  { key: "minute", label: "分时" },
  { key: "day", label: "日K" },
  { key: "week", label: "周K" },
  { key: "month", label: "月K" },
] as const
const period = ref("day")
const periodNote = ref("")

/**
 * 指数页签。这三条指数就是 `backend/app/catalog/reference_data.py` 里
 * `_CN_INDEXES` 全部入库的 INDEX 标的，不另外编造。
 */
const INDEX_TABS = [
  { key: "000001.SH", label: "上证指数" },
  { key: "399001.SZ", label: "深证成指" },
  { key: "399006.SZ", label: "创业板指" },
] as const

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

const subtitle = computed(() => {
  const item = instrument.value
  if (!item) {
    return symbol.value
  }
  const name = item.nameCn || item.nameEn || ""
  return `${item.displaySymbol} ${name} · ${item.instrumentType}`.trim()
})

const activeIndexTab = computed(() =>
  INDEX_TABS.some((tab) => tab.key === symbol.value.toUpperCase()) ? symbol.value.toUpperCase() : "",
)

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

/** 当日 OHLC 读数：只用最新一根已入库日线，不补价。 */
const ohlc = computed(() => {
  const bar = latest.value
  if (!bar) {
    return []
  }
  return [
    { label: "开", value: bar.open },
    { label: "高", value: bar.high },
    { label: "低", value: bar.low },
    { label: "收", value: bar.close },
  ]
})

const maReadings = computed(() => {
  const bar = latest.value
  if (!bar) {
    return []
  }
  return MA_WINDOWS.map((window) => ({
    window,
    text: maLabel(window, asString(bar[MA_FIELD[window]])),
    ready: asString(bar[MA_FIELD[window]]) !== null,
  }))
})

/** 成交量口径：单位来自后端，缺了就写「—」，不猜。 */
const volumeUnitText = computed(() => {
  const unit = latest.value?.volume_unit ?? ""
  return unit === "" ? "—" : unit === "hand" ? "手" : unit
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

/** 周期切换：只有日K 是后端支持的口径，其余只提示，不改请求。 */
function pickPeriod(key: string): void {
  period.value = key
  periodNote.value =
    key === "day" ? "" : "后端日线接口的 timeframe 目前只实现 1d，这里没有分时/周线/月线数据，不用日线拼接顶替。"
}

function pickIndex(key: string): void {
  const next = key.toUpperCase()
  if (next === symbol.value.toUpperCase()) {
    return
  }
  symbol.value = next
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
    <PageHeader :title="heading" :subtitle="subtitle">
      <template #tabs>
        <SectionTabs
          :tabs="INDEX_TABS.map((tab) => ({ key: tab.key, label: tab.label }))"
          :model-value="activeIndexTab"
          @update:model-value="pickIndex"
        />
      </template>
      <template #filters>
        <form class="filters-form" @submit.prevent="query">
          <label class="field">
            证券代码
            <input v-model="symbol" name="bar-symbol" placeholder="600519.SH" />
          </label>
          <label class="field">
            复权
            <select v-model="adjustment" name="bar-adjustment">
              <option value="none">不复权</option>
              <option value="qfq">前复权</option>
              <option value="hfq">后复权</option>
            </select>
          </label>
          <label class="field">
            开始
            <input v-model="start" name="bar-start" type="date" />
          </label>
          <label class="field">
            结束
            <input v-model="end" name="bar-end" type="date" />
          </label>
          <label class="field">
            观察日
            <input v-model="asOf" name="bar-as-of" type="date" />
          </label>
          <label class="field">
            均线
            <el-select v-model="activeMas" name="ma-selector" multiple collapse-tags style="width: 160px">
              <el-option v-for="window in MA_WINDOWS" :key="window" :label="`MA${window}`" :value="window" />
            </el-select>
          </label>
          <button class="retry" type="submit" :disabled="phase === 'loading'">查看日线</button>
        </form>
      </template>
    </PageHeader>

    <StateBlock
      v-if="phase === 'loading' || phase === 'idle'"
      state="loading"
      :rows="4"
    />

    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="query" />

    <template v-else>
      <div class="grid-1-2">
        <PanelCard
          title="日K线与成交量"
          :info="`${adjustmentLabel(adjustment)} · 成交量单位 ${volumeUnitText} · 不补缺失交易日`"
          flush
        >
          <div class="kline-head">
            <div class="period-bar" role="group" aria-label="周期切换">
              <button
                v-for="item in PERIODS"
                :key="item.key"
                type="button"
                class="period"
                :class="{ active: period === item.key }"
                @click="pickPeriod(item.key)"
              >
                {{ item.label }}
              </button>
            </div>
            <div v-if="latest" class="quote-line">
              <span class="close tabular">{{ latest.close }} {{ currency }}</span>
              <DeltaValue :value="change?.delta ?? null" :signed="true" />
              <DeltaValue :value="change?.percent ?? null" suffix="%" :signed="true" />
              <DeltaValue
                :value="asNumber(latest.simple_return) === null ? null : (asNumber(latest.simple_return) ?? 0) * 100"
                suffix="%"
                :signed="true"
                size="sm"
              />
              <span class="muted">{{ latest.trade_date }} 收盘</span>
            </div>
          </div>

          <p v-if="periodNote" class="period-note muted">{{ periodNote }}</p>

          <VChart
            v-if="bars.length"
            class="kline-chart"
            :option="chartOption"
            autoresize
            aria-label="日K线与成交量"
          />
          <p v-else class="kline-empty muted">{{ lines[0] ?? "这段时间没有入库日线。" }}</p>
        </PanelCard>

        <div class="stack">
          <PanelCard title="当日读数" info="只用最新一根已入库日线，未同步的日期不补价">
            <p v-if="!latest" class="muted">暂无已入库的日线，无法给出开高低收。</p>
            <template v-else>
              <dl class="ohlc">
                <div v-for="item in ohlc" :key="item.label">
                  <dt>{{ item.label }}</dt>
                  <dd class="tabular">{{ item.value }}</dd>
                </div>
              </dl>
              <ul class="ma-list">
                <li v-for="item in maReadings" :key="item.window" class="tabular" :class="{ 'is-weak': !item.ready }">
                  {{ item.text }}
                </li>
              </ul>
              <p class="muted ma-note">
                均线由后端按不复权收盘价滚动计算；样本不足时返回空值，这里照实写「样本不足」。
              </p>
            </template>
          </PanelCard>

          <PanelCard title="涨跌图例" info="A 股习惯：涨红跌绿，并同时给出正负号">
            <p class="legend-line">
              <span class="legend-dot rise" aria-hidden="true" />
              <span>涨（收 ≥ 开）</span>
              <span class="legend-dot fall" aria-hidden="true" />
              <span>跌（收 &lt; 开）</span>
            </p>
            <p class="muted">
              蜡烛体与成交量柱同色；不会因为缺少数据把 K 线补成一条直线。
            </p>
          </PanelCard>
        </div>
      </div>

      <PanelCard
        class="bars-card"
        title="入库明细"
        :info="`共 ${bars.length} 根日线`"
        flush
      >
        <div class="bars-scroll">
          <el-table :data="bars" size="small" stripe row-key="trade_date" max-height="360">
            <el-table-column prop="trade_date" label="交易日" width="120" fixed />
            <el-table-column prop="open" label="开" width="94" class-name="tabular" />
            <el-table-column prop="high" label="高" width="94" class-name="tabular" />
            <el-table-column prop="low" label="低" width="94" class-name="tabular" />
            <el-table-column prop="close" label="收" width="94" class-name="tabular" />
            <el-table-column prop="volume" label="成交量" width="120" class-name="tabular" />
            <el-table-column prop="currency" label="币种" width="80" />
            <el-table-column prop="ma5" label="MA5" width="94" class-name="tabular" />
            <el-table-column prop="ma10" label="MA10" width="94" class-name="tabular" />
            <el-table-column prop="ma20" label="MA20" width="94" class-name="tabular" />
            <el-table-column prop="ma60" label="MA60" width="94" class-name="tabular" />
          </el-table>
        </div>
      </PanelCard>

      <SourceFooter
        :as-of="latest ? `${latest.trade_date} 收盘` : '—'"
        :sources="
          instrument
            ? [instrument.exchange, 'A股日线（东方财富 / 腾讯行情，已入库）']
            : ['A股日线（东方财富 / 腾讯行情，已入库）']
        "
        :note="`${adjustmentLabel(adjustment)} · 缺失交易日不补 0，观察日之后的 K 线不显示；页面不承诺实时`"
      />
    </template>
  </main>
</template>

<style scoped>
.filters-form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.field {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

.field input,
.field select {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
}

.kline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px 0;
}

.period-bar {
  display: flex;
  gap: 4px;
}

.period {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  padding: 4px 14px;
  font-size: 13px;
  cursor: pointer;
}

.period.active {
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}

.quote-line {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.quote-line .close {
  font-size: 20px;
  font-weight: 600;
}

.period-note {
  margin: 8px 16px 0;
  font-size: 12px;
}

.kline-chart {
  width: 100%;
  height: 420px;
  margin-top: 8px;
}

.kline-empty {
  margin: 24px 16px;
}

.ohlc {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.ohlc dt {
  font-size: 12px;
  color: var(--muted);
}

.ohlc dd {
  margin: 2px 0 0;
  font-size: 18px;
  font-weight: 600;
}

.ma-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin: 16px 0 8px;
  padding: 0;
  list-style: none;
  font-size: 13px;
}

.ma-list .is-weak {
  color: var(--muted);
}

.ma-note {
  margin: 0;
  font-size: 12px;
}

.legend-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 8px;
  font-size: 13px;
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.legend-dot.rise {
  background: var(--rise);
}

.legend-dot.fall {
  background: var(--fall);
}

.bars-card {
  margin-top: var(--gap);
}

.bars-scroll {
  overflow-x: auto;
}
</style>
