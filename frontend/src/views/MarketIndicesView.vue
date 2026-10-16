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
import { Histogram } from "@element-plus/icons-vue"
import { VChart } from "../charts"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { adjustmentLabel, summarizeBars, volumeUnitLabel } from "../api/bars"
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

/** 最新一个交易日的原始读数，全部直接来自接口，不在前端二次计算比率。 */
const latestBar = computed<DailyBar | null>(() => (bars.value.length ? bars.value[bars.value.length - 1] ?? null : null))

/** 每个指数一张大卡用的迷你走势：取各自收盘价，缺失日期不补值。 */
function sparkPoints(item: SeriesState): number[] {
  return item.bars
    .map((bar) => asNumber(bar.close))
    .filter((value): value is number => value !== null)
    .slice(-60)
}

/** 指数切换页签，名称取数据库返回的 name_cn。 */
const indexTabs = computed(() =>
  INDEXES.map((item) => ({
    key: item.symbol,
    label: seriesMap.value[item.symbol]?.name ?? item.fallbackName,
  })),
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
            adjustment: "none",
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
          adjustment: asString(page.data?.adjustment) ?? "none",
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
          adjustment: "none",
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
  <div class="page">
    <PageHeader title="市场行情" subtitle="指数行情 · 只展示已入库的真实交易日，缺失日期不补价格">
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
      <template #filters>
        <div class="filter-row">
          <span class="filter-label">时间范围</span>
          <el-date-picker
            v-model="range"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="~"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            @change="load"
          />
          <el-button text @click="resetRange">近 {{ DEFAULT_DAYS }} 天</el-button>
        </div>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'error'" state="error" :message="errorMessage" @retry="load" />

    <template v-else>
      <!-- 四个指数各一张大卡 -->
      <div class="grid-4">
        <StatCard
          v-for="item in INDEXES"
          :key="item.symbol"
          :title="item.symbol"
          :value="seriesMap[item.symbol]?.bars.length ? (asNumber(seriesMap[item.symbol]!.bars[seriesMap[item.symbol]!.bars.length - 1]!.close)?.toFixed(2) ?? null) : null"
          :caption="seriesMap[item.symbol]?.problem ?? seriesMap[item.symbol]?.name ?? item.fallbackName"
          :info="seriesMap[item.symbol]?.name ?? item.fallbackName"
        >
          <template #icon><el-icon><Histogram /></el-icon></template>
          <template #chart>
            <MiniSpark
              v-if="sparkPoints(seriesMap[item.symbol]!).length > 1"
              :points="sparkPoints(seriesMap[item.symbol]!)"
              kind="area"
              tone="up"
              :width="88"
              :height="36"
            />
          </template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <PanelCard title="指数走势" info="K线与成交量全部来自 /market/bars，十字线对齐两个坐标轴">
          <template #actions>
            <SectionTabs :tabs="indexTabs" :model-value="activeSymbol" @update:model-value="changedSymbol" />
          </template>

          <StateBlock v-if="loading" state="loading" :rows="6" />
          <StateBlock v-else-if="bars.length === 0" state="empty" :message="emptyText || '该指数暂无已同步的日线'" @retry="load" />
          <template v-else>
            <div class="ohlc-row">
              <span class="ohlc-item">
                <em>交易日</em>
                <strong>{{ latestBar?.trade_date ?? "—" }}</strong>
              </span>
              <span class="ohlc-item"><em>开</em><strong>{{ latestBar?.open ?? "—" }}</strong></span>
              <span class="ohlc-item"><em>高</em><strong>{{ latestBar?.high ?? "—" }}</strong></span>
              <span class="ohlc-item"><em>低</em><strong>{{ latestBar?.low ?? "—" }}</strong></span>
              <span class="ohlc-item">
                <em>收</em>
                <strong>{{ latestBar?.close ?? "—" }}</strong>
                <DeltaValue :value="latest?.changePercent ?? null" suffix="%" size="sm" />
              </span>
              <span class="ohlc-item">
                <em>成交量</em>
                <strong>{{ volumeText(latestBar?.volume ?? "") }}</strong>
              </span>
              <span class="ohlc-item">
                <em>复权</em>
                <StatusPill :text="adjustmentLabel(active?.adjustment ?? 'none')" tone="muted" />
              </span>
            </div>
            <VChart class="chart-main" :option="chartOption" autoresize />
          </template>
        </PanelCard>

        <div class="stack">
          <PanelCard title="最新交易日读数" info="数值直接来自接口返回的日线字段，前端不做二次计算">
            <div class="tile-grid">
              <MetricTile label="开盘" :value="latestBar?.open ?? null" />
              <MetricTile label="最高" :value="latestBar?.high ?? null" />
              <MetricTile label="最低" :value="latestBar?.low ?? null" />
              <MetricTile label="收盘" :value="latestBar?.close ?? null" />
              <MetricTile
                label="成交量"
                :value="volumeText(latestBar?.volume ?? '')"
                :period="volumeUnitLabel(latestBar?.volume_unit)"
              />
              <MetricTile label="涨跌幅" :value="latest?.changePercent === null || latest?.changePercent === undefined ? null : latest.changePercent.toFixed(2)" unit="%" />
            </div>
          </PanelCard>

          <PanelCard title="移动均线" info="MA5/10/20/60 由后端按已入库日线计算，样本不足时为空">
            <div class="tile-grid">
              <MetricTile label="MA5" :value="latestBar?.ma5 ?? null" />
              <MetricTile label="MA10" :value="latestBar?.ma10 ?? null" />
              <MetricTile label="MA20" :value="latestBar?.ma20 ?? null" />
              <MetricTile label="MA60" :value="latestBar?.ma60 ?? null" />
            </div>
          </PanelCard>

          <PanelCard title="区间完整性" info="区间内没有日线的交易日不会被补齐，这是规格书要求的缺数处理">
            <ul class="plain-list">
              <li>
                <span>区间</span>
                <strong>{{ range[0] ?? "—" }} ~ {{ range[1] ?? "—" }}</strong>
              </li>
              <li>
                <span>返回条数</span>
                <strong class="tabular">{{ bars.length }}</strong>
              </li>
              <li>
                <span>是否截断</span>
                <StatusPill :text="active?.truncated ? '已截断' : '未截断'" :tone="active?.truncated ? 'warn' : 'ok'" />
              </li>
              <li>
                <span>数据模式</span>
                <StatusPill :text="meta.dataMode ?? '—'" :tone="demoMode ? 'warn' : 'ok'" />
              </li>
            </ul>
            <p v-if="emptyText" class="notice-text">{{ emptyText }}</p>
          </PanelCard>
        </div>
      </div>

      <PanelCard title="四个指数的入库情况" info="条数与最新交易日来自 /market/bars 的实际返回" class="section" flush>
        <el-table :data="INDEXES" size="small">
          <el-table-column prop="symbol" label="代码" width="120" />
          <el-table-column label="名称" min-width="120">
            <template #default="{ row }">{{ seriesMap[row.symbol]?.name ?? row.fallbackName }}</template>
          </el-table-column>
          <el-table-column label="最新交易日" width="140">
            <template #default="{ row }">
              {{ seriesMap[row.symbol]?.bars.length ? seriesMap[row.symbol]!.bars[seriesMap[row.symbol]!.bars.length - 1]!.trade_date : "—" }}
            </template>
          </el-table-column>
          <el-table-column label="区间条数" width="120" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ seriesMap[row.symbol]?.bars.length ?? 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="{ row }">
              <StatusPill
                :text="seriesMap[row.symbol]?.problem ? '缺数据' : '已入库'"
                :tone="seriesMap[row.symbol]?.problem ? 'warn' : 'ok'"
                dot
              />
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">{{ seriesMap[row.symbol]?.problem ?? "—" }}</template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <SourceFooter
        :as-of="asOfText"
        :sources="meta.source ? [meta.source] : []"
        :stale="meta.stale"
        :note="demoMode ? `当前数据模式：${meta.dataMode}` : ''"
      />
    </template>
  </div>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.filter-label {
  font-size: 12px;
  color: var(--muted);
}

.chart-main {
  width: 100%;
  height: 380px;
  margin-top: 12px;
}

.ohlc-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-soft);
}

.ohlc-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  font-size: 13px;
}

.ohlc-item em {
  font-style: normal;
  font-size: 12px;
  color: var(--muted);
}

.ohlc-item strong {
  font-variant-numeric: tabular-nums;
}

.tile-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.plain-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.plain-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
}

.plain-list strong {
  color: var(--text);
  font-weight: 500;
}

.notice-text {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
</style>
