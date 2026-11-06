<script setup lang="ts">
/**
 * 右侧「指数详情」：在「全球核心指数与大宗商品」里点某张卡片后，替换个股详情显示该指数 / 商品。
 * 只有行情头、开高低收、分时、日K：没有封单、涨停原因、韭研解析、龙虎榜席位这些个股专属的东西。
 *
 * 数据真实：均价、成交量上游没给就不画均价线 / 量柱；黄金、原油拿不到分时时显示空状态和上游给的原因，不造数。
 * 美股分时跨北京时间午夜（22:30–05:00），按接口返回的顺序画，不按 HH:MM 排序。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import type { EChartsOption } from "echarts"

import { errorMessage, fetchIndexDetail, type IndexDetailData } from "../../api/aSharePanorama"
import { EMPTY_TEXT, fmtPct, fmtYi, toneClass } from "../../api/aShareFormat"
import { VChart } from "../../charts"
import { useMarketLinkageStore } from "../../stores/marketLinkage"
import StateBlock from "../ui/StateBlock.vue"
import { buildIntradayOption, buildKlineOption, hasValues } from "./detailCharts"
import PanoramaStatusBar from "./PanoramaStatusBar.vue"
import ResizableBox from "./ResizableBox.vue"

const POLL_MS = 60_000

const linkageStore = useMarketLinkageStore()
const detail = ref<IndexDetailData | null>(null)
const loadState = ref<"loading" | "error">("loading")
const loadError = ref("")
const chartMode = ref<"both" | "intraday" | "kline">("both")
let loadSeq = 0
let timer: number | null = null

/** silent=true 是定时刷新：不闪骨架，失败了保留旧数据。 */
async function loadDetail(code: string, silent = false): Promise<void> {
  if (!code) {
    detail.value = null
    return
  }
  const seq = ++loadSeq
  if (!silent) {
    loadState.value = "loading"
    loadError.value = ""
    // 切指数时先清空，避免短暂显示上一个品种的数据
    if (detail.value && detail.value.code !== code) detail.value = null
  }
  try {
    const result = await fetchIndexDetail(code)
    if (seq === loadSeq) detail.value = result
  } catch (e) {
    if (seq !== loadSeq) return
    if (silent && detail.value) return
    detail.value = null
    loadState.value = "error"
    loadError.value = errorMessage(e)
  }
}

watch(
  () => linkageStore.activeIndexCode,
  (next) => void loadDetail(next),
)

onMounted(() => {
  void loadDetail(linkageStore.activeIndexCode)
  timer = window.setInterval(() => {
    if (!document.hidden) void loadDetail(linkageStore.activeIndexCode, true)
  }, POLL_MS)
})

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
  timer = null
})

const categoryClass = computed(() => {
  const category = detail.value?.category
  if (category === "海外指数") return "cat-overseas"
  if (category === "大宗商品") return "cat-commodity"
  return "cat-cn"
})

/** 今开 / 最高 / 最低 相对昨收的涨跌幅；昨收缺失时不算。 */
function pctVsPreClose(value: number | null): number | null {
  const pre = detail.value?.preClose
  if (value === null || pre === null || pre === undefined || pre === 0) return null
  return ((value - pre) / pre) * 100
}

function fmtPrice(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return EMPTY_TEXT
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** 涨跌额：带正负号，千分位和现价保持一致（如 -1,421.51）。 */
function fmtDelta(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return EMPTY_TEXT
  const text = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (value > 0) return "+" + text
  return value < 0 ? "-" + text : text
}

function levelCell(label: string, value: number | null) {
  const pct = pctVsPreClose(value)
  return {
    label,
    text: fmtPrice(value),
    tone: toneClass(pct),
    sub: pct === null ? "" : `较昨收 ${fmtPct(pct)}`,
  }
}

const metrics = computed(() => {
  const d = detail.value
  if (!d) return []
  return [
    levelCell("今开", d.open),
    levelCell("最高", d.high),
    levelCell("最低", d.low),
    { label: "昨收", text: fmtPrice(d.preClose), tone: "", sub: "" },
    { label: "涨跌额", text: fmtDelta(d.change), tone: toneClass(d.change), sub: "" },
    {
      label: "成交额",
      text: d.amountYuan === null ? EMPTY_TEXT : fmtYi(d.amountYuan / 1e8, 0),
      tone: "",
      sub: "",
    },
  ]
})

const intradayHasAvg = computed(() => hasValues(detail.value?.intraday.map((p) => p.avgPrice) ?? []))

const intradayOption = computed<EChartsOption>(() => {
  const pts = detail.value?.intraday ?? []
  return buildIntradayOption({
    times: pts.map((p) => p.time),
    prices: pts.map((p) => p.price),
    avgs: pts.map((p) => p.avgPrice),
    vols: pts.map((p) => p.volume),
    isUp: (detail.value?.changePct ?? 0) >= 0,
  })
})

const klineOption = computed<EChartsOption>(() => buildKlineOption(detail.value?.dailyBars ?? []))

/** 美股、港股、商品的分时是北京时间，在图卡标题旁标出来，避免被当成当地时间。 */
const intradayTimeHint = computed(() => (detail.value && detail.value.category !== "A股宽基" ? "北京时间" : ""))

const intradayEmptyMessage = computed(
  () => detail.value?.meta.unavailable["指数分时"] || "暂无分时数据（休市或上游未返回）",
)
const klineEmptyMessage = computed(() => detail.value?.meta.unavailable["指数日K"] || "暂无日K数据")
</script>

<template>
  <aside class="stock-detail-panel index-detail-panel" aria-label="右侧指数详情联动面板">
    <StateBlock
      v-if="!detail"
      :state="loadState"
      :message="loadError"
      title="指数详情加载失败"
      :rows="5"
      @retry="loadDetail(linkageStore.activeIndexCode)"
    />

    <template v-else>
      <div class="sdp-header">
        <div class="sdp-title-row">
          <div class="sdp-stock-id">
            <span class="sdp-name">{{ detail.name || detail.code }}</span>
            <span class="sdp-code">{{ detail.code }}</span>
            <span class="idx-cat" :class="categoryClass">{{ detail.category }}</span>
          </div>
          <div class="sdp-price-box" :class="toneClass(detail.changePct)">
            <span class="sdp-price">{{ fmtPrice(detail.price) }}</span>
            <span class="sdp-chg">{{ fmtPct(detail.changePct) }}</span>
          </div>
        </div>
      </div>

      <div class="sdp-metrics-grid">
        <div v-for="m in metrics" :key="m.label" class="sdp-metric-cell">
          <span class="m-label">{{ m.label }}</span>
          <span class="m-val" :class="m.tone">{{ m.text }}</span>
          <span class="m-sub">{{ m.sub }}</span>
        </div>
      </div>

      <!-- 分时走势图 & 日K线图 -->
      <div class="sdp-chart-section">
        <div class="sdp-chart-tabs">
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'both' }"
            @click="chartMode = 'both'"
          >
            分时 + 日K
          </button>
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'intraday' }"
            @click="chartMode = 'intraday'"
          >
            分时
          </button>
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'kline' }"
            @click="chartMode = 'kline'"
          >
            日K（MA5/10/20）
          </button>
        </div>

        <ResizableBox v-if="chartMode === 'both' || chartMode === 'intraday'" id="detail.intraday" axes="h" :min-h="150">
          <div class="sdp-chart-card">
            <div class="chart-mini-head">
              <span class="chart-mini-title">
                当日分时走势<template v-if="intradayTimeHint">（{{ intradayTimeHint }}）</template>
              </span>
              <span v-if="detail.intraday.length" class="chart-mini-legend">
                <span><i class="dot-price" /> 现价线</span>
                <span v-if="intradayHasAvg"><i class="dot-avg" /> 均价线</span>
              </span>
            </div>
            <div class="sdp-echart-wrap" :class="{ single: chartMode !== 'both' }">
              <VChart
                v-if="detail.intraday.length"
                class="sdp-echart"
                :option="intradayOption"
                autoresize
              />
              <StateBlock v-else state="empty" :message="intradayEmptyMessage" />
            </div>
          </div>
        </ResizableBox>

        <ResizableBox v-if="chartMode === 'both' || chartMode === 'kline'" id="detail.kline" axes="h" :min-h="150">
          <div class="sdp-chart-card">
            <div class="chart-mini-head">
              <span class="chart-mini-title">日K线（近 {{ detail.dailyBars.length || 90 }} 根，含 MA 均线）</span>
              <span class="chart-mini-legend">
                <span class="ma5-txt">MA5</span>
                <span class="ma10-txt">MA10</span>
                <span class="ma20-txt">MA20</span>
              </span>
            </div>
            <div class="sdp-echart-wrap" :class="{ single: chartMode !== 'both' }">
              <VChart
                v-if="detail.dailyBars.length"
                class="sdp-echart"
                :option="klineOption"
                autoresize
              />
              <StateBlock v-else state="empty" :message="klineEmptyMessage" />
            </div>
          </div>
        </ResizableBox>
      </div>

      <footer class="sdp-foot">
        <PanoramaStatusBar :meta="detail.meta" />
      </footer>
    </template>
  </aside>
</template>

<style scoped>
/* 外壳、头部、指标格、图表卡的样式和个股详情保持一致（两个面板同一个位置轮流出现） */
.stock-detail-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 28px);
  overflow-y: auto;
}

.sdp-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.sdp-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sdp-stock-id {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sdp-name {
  font-size: 17px;
  font-weight: 700;
  color: var(--text);
}

.sdp-code {
  font-family: var(--font-num);
  font-size: 12px;
  color: var(--faint);
}

.idx-cat {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid transparent;
}

.idx-cat.cat-cn {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
}

.idx-cat.cat-overseas {
  color: var(--cyan);
  background: color-mix(in srgb, var(--cyan) 12%, transparent);
  border-color: color-mix(in srgb, var(--cyan) 35%, transparent);
}

.idx-cat.cat-commodity {
  color: var(--orange);
  background: color-mix(in srgb, var(--orange) 12%, transparent);
  border-color: color-mix(in srgb, var(--orange) 35%, transparent);
}

.sdp-price-box {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: var(--font-num);
}

.sdp-price {
  font-size: 18px;
  font-weight: 700;
}

.sdp-chg {
  font-size: 13px;
  font-weight: 600;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

.sdp-metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.sdp-metric-cell {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.m-label {
  font-size: 11px;
  color: var(--faint);
}

.m-val {
  font-family: var(--font-num);
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
}

.m-val.is-rise {
  color: var(--rise);
}

.m-val.is-fall {
  color: var(--fall);
}

.m-sub {
  min-height: 14px;
  font-size: 10.5px;
  color: var(--muted);
  white-space: nowrap;
}

.sdp-chart-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sdp-chart-tabs {
  display: flex;
  gap: 6px;
}

.chart-tab-btn {
  flex: 1;
  padding: 5px 8px;
  font-size: 11px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
}

.chart-tab-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.35);
  font-weight: 600;
}

.sdp-chart-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px;
  background: var(--panel-soft);
}

.chart-mini-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.chart-mini-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text);
}

.chart-mini-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10.5px;
  color: var(--faint);
}

.dot-price,
.dot-avg {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.dot-price {
  background: #f6465d;
}

.dot-avg {
  background: #f59e0b;
}

.ma5-txt {
  color: #f59e0b;
}

.ma10-txt {
  color: #3b82f6;
}

.ma20-txt {
  color: #a855f7;
}

.sdp-echart-wrap {
  height: 176px;
  width: 100%;
}

/* 只看一张图时给更大的默认高度 */
.sdp-echart-wrap.single {
  height: 280px;
}

.sdp-echart {
  width: 100%;
  height: 100%;
}

.sdp-foot {
  padding-top: 8px;
  border-top: 1px solid var(--line-soft);
}

/* —— 可拖高度的小框：分时图 / 日K图卡片（和个股详情共用同一组记录） —— */
.mk-box.is-h > .sdp-chart-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.mk-box.is-h > .sdp-chart-card > .chart-mini-head {
  flex: none;
}

/* 用户拖高了卡片：图表吃掉多出来的高度，ECharts 跟着重画 */
.mk-box.is-h .sdp-echart-wrap {
  flex: 1 1 auto;
  height: auto;
  min-height: 90px;
}
</style>
