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
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { adjustmentLabel, summarizeBars } from "../api/bars"
import type { DailyBar } from "../api/bars"
import { asNumber, asRecord, asString, dataOf, formatMoment } from "../api/json"
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

interface SeriesState {
  symbol: string
  name: string
  instrumentId: string
  bars: DailyBar[]
  emptyMessage: string | null
  truncated: boolean
  problem: string | null
}

interface MetaInfo {
  asOf: string | null
  source: string | null
  stale: boolean
  dataMode: string | null
  requestId: string | null
}

const phase = ref<Phase>("loading")
const errorMessage = ref("")
const errorCode = ref("")
const requestId = ref("")
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

const active = computed<SeriesState | null>(() => seriesMap.value[activeSymbol.value] ?? null)
const bars = computed<DailyBar[]>(() => active.value?.bars ?? [])
const asOfText = computed(() => formatMoment(meta.value.asOf))
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")
const emptyText = computed(() =>
  summarizeBars({ emptyMessage: active.value?.emptyMessage ?? null, bars: bars.value })[0] ?? "",
)

/** 收盘价与相对上一交易日的涨跌：正数带 + 号，跌绿涨红，颜色不是唯一区分手段。 */
const latest = computed(() => {
  const rows = bars.value
  if (rows.length === 0) {
    return null
  }
  const last = rows[rows.length - 1]
  const previous = rows.length > 1 ? rows[rows.length - 2] : null
  if (!last) {
    return null
  }
  const lastClose = asNumber(last.close)
  if (lastClose === null) {
    return null
  }
  const previousClose = previous ? asNumber(previous.close) : null
  let change: number | null = null
  let changePercent: number | null = null
  if (previousClose !== null && previousClose !== 0) {
    change = lastClose - previousClose
    changePercent = (change / previousClose) * 100
  }
  return {
    tradeDate: last.trade_date,
    close: lastClose,
    change,
    changePercent,
    currency: last.currency,
    adjustment: last,
  }
})

function signed(value: number | null, digits: number, suffix: string): string {
  if (value === null) {
    return "—"
  }
  const sign = value > 0 ? "+" : ""
  return `${sign}${value.toFixed(digits)}${suffix}`
}

function trendClass(value: number | null): string {
  if (value === null) {
    return "muted"
  }
  if (value > 0) {
    return "rise"
  }
  if (value < 0) {
    return "fall"
  }
  return "muted"
}

function percentText(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(2)}%`
}

function volumeText(value: string): string {
  const parsed = asNumber(value)
  if (parsed === null) {
    return "—"
  }
  if (Math.abs(parsed) >= 100000000) {
    return `${(parsed / 100000000).toFixed(2)} 亿`
  }
  if (Math.abs(parsed) >= 10000) {
    return `${(parsed / 10000).toFixed(2)} 万`
  }
  return parsed.toFixed(0)
}

/** K 线 + 成交量：主轴一张图两个 grid，数据只来自已入库日线。 */
const chartOption = computed<EChartsOption>(() => {
  const rows = bars.value
  const dates = rows.map((bar) => bar.trade_date)
  const candles = rows.map((bar) => [
    asNumber(bar.open) ?? 0,
    asNumber(bar.close) ?? 0,
    asNumber(bar.low) ?? 0,
    asNumber(bar.high) ?? 0,
  ])
  const volumes = rows.map((bar, index) => ({
    value: asNumber(bar.volume) ?? 0,
    itemStyle: {
      color: (asNumber(bar.close) ?? 0) >= (asNumber(bar.open) ?? 0) ? "#f87171" : "#34d399",
    },
    name: dates[index] ?? "",
  }))
  const closes = rows.map((bar) => asNumber(bar.close))
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["K线", "收盘价"], textStyle: { color: "#95a6be" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 60, right: 20, top: 40, height: 220 },
      { left: 60, right: 20, top: 290, height: 70 },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLabel: { color: "#95a6be", rotate: 45 },
        axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: dates,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
      },
    ],
    yAxis: [
      {
        type: "value",
        scale: true,
        name: active.value?.name ?? "",
        nameTextStyle: { color: "#95a6be" },
        axisLabel: { color: "#95a6be" },
        splitLine: { lineStyle: { color: "rgba(149,166,190,0.15)" } },
      },
      {
        type: "value",
        gridIndex: 1,
        name: "成交量",
        nameTextStyle: { color: "#95a6be" },
        axisLabel: { color: "#95a6be" },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 40, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 18, textStyle: { color: "#95a6be" } },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: candles,
        itemStyle: {
          color: "#f87171",
          color0: "#34d399",
          borderColor: "#f87171",
          borderColor0: "#34d399",
        },
      },
      {
        name: "收盘价",
        type: "line",
        data: closes,
        smooth: false,
        symbol: "none",
        lineStyle: { color: "#3b82f6", width: 1 },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barMaxWidth: 12,
      },
    ],
  }
})

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError("无法连接接口", "NETWORK_ERROR", null)
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

async function load(): Promise<void> {
  loading.value = true
  phase.value = "loading"
  errorMessage.value = ""
  errorCode.value = ""
  seriesMap.value = {}

  const start = range.value[0] ?? ""
  const end = range.value[1] ?? ""
  const rangeQuery = new URLSearchParams()
  if (start) {
    rangeQuery.set("start", start)
  }
  if (end) {
    rangeQuery.set("end", end)
  }

  const next: Record<string, SeriesState> = {}
  let firstMeta: MetaInfo | null = null
  let failure: ApiError | null = null

  try {
    for (const item of INDEXES) {
      const symbol = item.symbol
      try {
        const found = await getJson(
          `/api/v1/instruments/search?q=${symbol.slice(0, 6)}&market=CN`,
        )
        firstMeta = firstMeta ?? found.meta
        const hit = (Array.isArray(found.data?.results) ? found.data.results : [])
          .flatMap((row) => {
            const record = asRecord(row)
            return record ? [record] : []
          })
          .find((record) => asString(record.display_symbol) === symbol)
        const instrumentId = asString(hit?.id)
        if (!hit || !instrumentId) {
          next[symbol] = {
            symbol,
            name: item.fallbackName,
            instrumentId: "",
            bars: [],
            emptyMessage: "该指数未入库",
            truncated: false,
            problem: "该指数未入库",
          }
          continue
        }
        const query = rangeQuery.size ? `?${rangeQuery.toString()}` : ""
        const page = await getJson(`/api/v1/market/bars/${instrumentId}${query}`)
        firstMeta = firstMeta ?? page.meta
        const rows = (Array.isArray(page.data?.bars) ? page.data.bars : []).flatMap((row) => {
          const record = asRecord(row)
          return record ? [toBar(record)] : []
        })
        next[symbol] = {
          symbol,
          name: asString(page.data?.name_cn) ?? asString(hit.name_cn) ?? item.fallbackName,
          instrumentId,
          bars: rows,
          emptyMessage: asString(page.data?.empty_message),
          truncated: page.data?.truncated === true,
          problem: null,
        }
      } catch (error) {
        const apiError = toApiError(error)
        failure = failure ?? apiError
        next[symbol] = {
          symbol,
          name: item.fallbackName,
          instrumentId: "",
          bars: [],
          emptyMessage: null,
          truncated: false,
          problem: apiError.message,
        }
      }
    }
    seriesMap.value = next
    if (firstMeta) {
      meta.value = firstMeta
      requestId.value = firstMeta.requestId ?? ""
    }
    // 四个指数全部失败才算整页错误；部分失败会在对应指数上标注原因。
    const allFailed = Object.values(next).every((item) => item.problem !== null)
    if (allFailed && failure) {
      errorMessage.value = failure.message
      errorCode.value = failure.code
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

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <header class="page-head">
      <div>
        <h1>指数行情</h1>
        <p class="muted">
          只展示已入库的四个中国指数日线（不复权）；未同步的日期不会用演示价格填补。
        </p>
      </div>
      <div class="range-bar">
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          range-separator="至"
          :clearable="false"
        />
        <el-button :loading="loading" @click="load">查询</el-button>
        <el-button text :loading="loading" @click="resetRange">最近 90 天</el-button>
      </div>
    </header>

    <p v-if="phase === 'ready'" class="meta-line muted">
      数据时间 {{ asOfText }} · 来源 {{ meta.source ?? "—" }} · 请求号 {{ requestId || "—" }}
    </p>

    <div v-if="demoMode" class="banner" role="status">
      当前返回的是{{ meta.dataMode }}数据，不是实时行情，请勿据此下单。
    </div>
    <div v-if="phase === 'ready' && meta.stale" class="banner" role="status">
      数据已过期（stale），展示的是上次成功抓取的结果。
    </div>

    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取指数日线</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法加载指数行情</h1>
      <p>{{ errorMessage }}</p>
      <p v-if="errorCode" class="muted">错误码 {{ errorCode }} · 请求号 {{ requestId || "—" }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <section class="panel">
        <div class="panel-head">
          <h2>指数列表</h2>
          <span class="muted">点击切换下方图表与明细</span>
        </div>
        <div class="index-grid">
          <button
            v-for="item in INDEXES"
            :key="item.symbol"
            type="button"
            class="index-card card"
            :class="{ active: item.symbol === activeSymbol }"
            :aria-pressed="item.symbol === activeSymbol"
            @click="changedSymbol(item.symbol)"
          >
            <span class="index-name">
              {{ seriesMap[item.symbol]?.name ?? item.fallbackName }}
              <span class="muted">{{ item.symbol }}</span>
            </span>
            <span class="muted">
              日线 {{ seriesMap[item.symbol]?.bars.length ?? 0 }} 条
            </span>
            <span
              v-if="seriesMap[item.symbol]?.problem"
              class="chip chip-warn"
            >
              {{ seriesMap[item.symbol]?.problem }}
            </span>
            <span
              v-else-if="!(seriesMap[item.symbol]?.bars.length ?? 0)"
              class="chip chip-warn"
            >
              暂无已同步的日线
            </span>
            <span v-else class="chip chip-ok">已有日线</span>
          </button>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>
            {{ active?.name ?? "指数" }}
            <span class="muted">{{ activeSymbol }}</span>
          </h2>
          <span v-if="bars.length" class="muted">
            不复权 · 展示 {{ bars.length }} 个交易日
          </span>
        </div>

        <div v-if="latest" class="latest-row">
          <div>
            <p class="muted">最新收盘（{{ latest.tradeDate }}）</p>
            <p class="state" :class="trendClass(latest.change)">
              {{ latest.close.toFixed(2) }} {{ latest.currency }}
            </p>
          </div>
          <div>
            <p class="muted">较上一交易日</p>
            <p class="state" :class="trendClass(latest.change)">
              {{ signed(latest.change, 2, "") }}
              <span v-if="latest.changePercent !== null">
                （{{ signed(latest.changePercent, 2, "%") }}）
              </span>
            </p>
          </div>
        </div>

        <p v-if="active?.truncated" class="banner" role="status">
          日线条数达到后端返回上限，图表只显示截取到的部分。
        </p>

        <template v-if="bars.length">
          <VChart class="chart" :option="chartOption" autoresize />
          <el-table :data="bars" size="small" stripe max-height="360">
            <el-table-column prop="trade_date" label="交易日" width="120" />
            <el-table-column prop="open" label="开盘" width="100" />
            <el-table-column prop="high" label="最高" width="100" />
            <el-table-column prop="low" label="最低" width="100" />
            <el-table-column prop="close" label="收盘" width="100" />
            <el-table-column label="成交量" width="120">
              <template #default="{ row }">
                {{ volumeText(row.volume) }} {{ row.volume_unit === "hand" ? "手" : row.volume_unit }}
              </template>
            </el-table-column>
            <el-table-column label="MA5" width="90">
              <template #default="{ row }">{{ row.ma5 ?? "样本不足" }}</template>
            </el-table-column>
            <el-table-column label="MA10" width="90">
              <template #default="{ row }">{{ row.ma10 ?? "样本不足" }}</template>
            </el-table-column>
            <el-table-column label="MA20" width="90">
              <template #default="{ row }">{{ row.ma20 ?? "样本不足" }}</template>
            </el-table-column>
            <el-table-column label="MA60" width="90">
              <template #default="{ row }">{{ row.ma60 ?? "样本不足" }}</template>
            </el-table-column>
            <el-table-column label="区间收益" width="110">
              <template #default="{ row }">{{ percentText(asNumber(row.simple_return)) }}</template>
            </el-table-column>
          </el-table>
        </template>

        <template v-else>
          <el-empty :description="emptyText" />
          <p v-if="active?.problem" class="muted">{{ active.problem }}</p>
          <p v-else class="muted">
            {{ adjustmentLabel("none") }}口径的日线尚未同步；同步任务完成后这里会出现 K 线。
          </p>
        </template>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 22px;
}

.range-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.meta-line {
  margin: 0 0 16px;
  word-break: break-all;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-head h2 {
  margin: 0;
  font-size: 17px;
}

.index-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  margin-top: 12px;
}

.index-card {
  display: grid;
  gap: 6px;
  text-align: left;
  cursor: pointer;
  color: var(--text);
  background: var(--panel);
  border: 1px solid var(--line);
  min-height: 0;
}

.index-card.active {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent) inset;
}

.index-name {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
}

.latest-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  margin: 12px 0;
}

.state {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.chip {
  display: inline-block;
  padding: 2px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12px;
  color: var(--muted);
}

.chip-ok {
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
}

.chip-warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
}

.chart {
  width: 100%;
  height: 420px;
  margin-bottom: 12px;
}

@media (min-width: 768px) {
  .index-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .latest-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 1280px) {
  .index-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
</style>
