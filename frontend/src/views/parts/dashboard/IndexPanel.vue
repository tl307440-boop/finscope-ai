<script setup lang="ts">
/** A 股主要指数走势：腾讯指数快照 + 分时 / 日K / 周K / 月K；右侧是选中指数的今日概览与两市全景。 */
import { computed, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import { DataAnalysis } from "@element-plus/icons-vue"
import { VChart } from "../../../charts"
import {
  PERIOD_OPTIONS,
  fetchIndexBars,
  type IndexBars,
  type IndexPeriod,
  type MarketOverview,
} from "../../../api/dashboard"
import { sectionBadge } from "../../../api/industryMarket"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { klineOption, minuteOption } from "./charts"
import { yuan } from "../industry/chartKit"

const props = defineProps<{
  market: MarketOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: [] }>()

const symbol = ref("sh000001")
const period = ref<IndexPeriod>("day")
const cache = ref<Record<string, IndexBars>>({})
const failures = ref<Record<string, string>>({})
const pending = ref<Record<string, boolean>>({})

const key = (code: string, span: IndexPeriod): string => `${code}:${span}`

async function ensure(code: string, span: IndexPeriod): Promise<void> {
  const id = key(code, span)
  if (cache.value[id] || pending.value[id]) {
    return
  }
  pending.value = { ...pending.value, [id]: true }
  try {
    const bars = await fetchIndexBars(code, span)
    cache.value = { ...cache.value, [id]: bars }
    failures.value = { ...failures.value, [id]: "" }
  } catch (error) {
    failures.value = { ...failures.value, [id]: error instanceof Error ? error.message : "指数行情暂时不可用" }
  } finally {
    pending.value = { ...pending.value, [id]: false }
  }
}

function retryBars(): void {
  const id = key(symbol.value, period.value)
  failures.value = { ...failures.value, [id]: "" }
  void ensure(symbol.value, period.value)
}

watch(
  [symbol, period],
  ([code, span]) => {
    void ensure(code, span)
    if (span !== "day") {
      void ensure(code, "day")
    }
  },
  { immediate: true },
)

const current = computed<IndexBars | null>(() => cache.value[key(symbol.value, period.value)] ?? null)
const currentError = computed(() => failures.value[key(symbol.value, period.value)] ?? "")
const currentPending = computed(() => pending.value[key(symbol.value, period.value)] ?? false)
const daily = computed<IndexBars | null>(() => cache.value[key(symbol.value, "day")] ?? null)
const quote = computed(() => props.market?.indices.find((item) => item.symbol === symbol.value) ?? null)
const badge = computed(() => (props.market ? sectionBadge(props.market.quotesSection) : null))

const option = computed<EChartsOption | null>(() => {
  const bars = current.value
  if (!bars) {
    return null
  }
  if (period.value === "min") {
    return bars.minutes.length > 1 ? minuteOption(bars.minutes, bars.prevClose) : null
  }
  return bars.bars.length > 1 ? klineOption(bars.bars, period.value) : null
})

const emptyText = computed(() => {
  if (period.value === "min") {
    return current.value?.section.reason || "今日尚未开盘，暂无分时数据"
  }
  return current.value?.section.reason || "该指数暂无 K 线数据"
})

const breadth = computed(() => props.market?.breadth ?? null)
const limits = computed(() => props.market?.limits ?? null)
const share = (part: number): string => {
  const total = breadth.value?.total ?? 0
  return total > 0 ? `${(part / total) * 100}%` : "0%"
}
</script>

<template>
  <PanelCard title="A股主要指数走势" info="指数快照与 K 线来自腾讯行情（不复权）；MA5 / MA10 / MA20、成交额、振幅由后端从日线计算。分时纵轴右侧为相对昨收涨跌幅。">
    <template #icon><el-icon><DataAnalysis /></el-icon></template>
    <template #actions>
      <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
      <el-radio-group v-model="period" size="small">
        <el-radio-button v-for="item in PERIOD_OPTIONS" :key="item.key" :value="item.key">{{ item.label }}</el-radio-button>
      </el-radio-group>
    </template>

    <StateBlock v-if="loading && !market" state="loading" :rows="6" />
    <StateBlock v-else-if="error && !market" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="market">
      <p v-if="market.quotesSection.status !== 'ok' && market.quotesSection.reason" class="notice">{{ market.quotesSection.reason }}</p>
      <div class="chips">
        <button
          v-for="item in market.indices"
          :key="item.symbol"
          type="button"
          class="chip"
          :class="{ on: item.symbol === symbol }"
          @click="symbol = item.symbol"
        >
          <span class="chip-name">{{ item.name }}</span>
          <span class="chip-line">
            <DeltaValue :value="item.last" :colored="false" size="lg" />
            <DeltaValue :value="item.changePct" suffix="%" size="sm" />
          </span>
        </button>
      </div>

      <div class="index-body">
        <div class="chart-wrap">
          <StateBlock v-if="currentPending && !current" state="loading" :rows="8" />
          <StateBlock v-else-if="currentError" state="error" :message="currentError" @retry="retryBars" />
          <VChart v-else-if="option" :key="`${symbol}-${period}`" class="chart" :option="option" autoresize />
          <StateBlock v-else-if="current" state="empty" :message="emptyText" />
        </div>

        <aside class="overview">
          <h3>今日行情概览<span v-if="quote">（{{ quote.name }}）</span></h3>
          <template v-if="quote">
            <div class="ov-row"><span>今开</span><DeltaValue :value="quote.open" :colored="false" /></div>
            <div class="ov-row"><span>最高</span><span class="num up">{{ quote.high?.toFixed(2) ?? "—" }}</span></div>
            <div class="ov-row"><span>最低</span><span class="num down">{{ quote.low?.toFixed(2) ?? "—" }}</span></div>
            <div class="ov-row"><span>最新</span><DeltaValue :value="quote.last" :colored="false" /></div>
            <div class="ov-row">
              <span>涨跌</span>
              <span><DeltaValue :value="quote.change" /> （<DeltaValue :value="quote.changePct" suffix="%" />）</span>
            </div>
            <div class="ov-row"><span>振幅</span><span class="num">{{ quote.amplitudePct === null ? "—" : `${quote.amplitudePct.toFixed(2)}%` }}</span></div>
            <div class="ov-row"><span>成交额</span><span class="num">{{ yuan(quote.amount) }}</span></div>
            <div class="ov-row"><span>较前 5 日均额</span><DeltaValue :value="daily?.amountVs5dPct ?? null" suffix="%" :digits="1" /></div>
          </template>
          <p v-else class="faint">该指数暂无快照</p>

          <h3 class="gap">两市全景<span v-if="market.tradeDate" class="faint">（{{ market.tradeDate }}）</span></h3>
          <div class="ov-row"><span>两市成交额</span><span class="num">{{ yuan(market.marketAmount) }}</span></div>
          <div class="ov-row"><span>较前 20 日均额</span><DeltaValue :value="market.amountVsBasePct" suffix="%" :digits="1" /></div>
          <template v-if="breadth">
            <div class="stack-bar" :title="`共 ${breadth.total} 只`">
              <i :style="{ width: share(breadth.up), background: 'var(--rise)' }" />
              <i :style="{ width: share(breadth.flat), background: '#64748b' }" />
              <i :style="{ width: share(breadth.down), background: 'var(--fall)' }" />
            </div>
            <div class="ov-row plain">
              <span class="num up">涨 {{ breadth.up }}</span>
              <span class="num faint">平 {{ breadth.flat }}</span>
              <span class="num down">跌 {{ breadth.down }}</span>
            </div>
          </template>
          <p v-else class="faint">{{ market.breadthSection.reason || "涨跌分布暂不可用" }}</p>
          <template v-if="limits">
            <div class="ov-row">
              <span>涨停 / 炸板 / 跌停</span>
              <span class="num"><span class="up">{{ limits.limitUp }}</span> / {{ limits.broken ?? "—" }} / <span class="down">{{ limits.limitDown ?? "—" }}</span></span>
            </div>
            <div class="ov-row">
              <span>封板率 · 最高连板</span>
              <span class="num">{{ limits.sealRate === null ? "—" : `${limits.sealRate.toFixed(2)}%` }} · {{ limits.maxStreak }} 板</span>
            </div>
          </template>
          <p v-else class="faint">{{ market.limitsSection.reason || "涨停池暂不可用" }}</p>
        </aside>
      </div>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.chips {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.chip {
  display: grid;
  gap: 2px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-deep);
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.chip.on {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent) inset;
  background: color-mix(in srgb, var(--accent) 10%, var(--panel-deep));
}

.chip-name {
  font-size: 12px;
  color: var(--muted);
}

.chip-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-wrap: wrap;
}

.index-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 230px;
  gap: 12px;
}

.chart-wrap {
  min-height: 380px;
}

.chart {
  width: 100%;
  height: 380px;
}

.overview {
  padding-left: 12px;
  border-left: 1px solid var(--line-soft);
  font-size: 13px;
}

.overview h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.overview h3.gap {
  margin-top: 12px;
}

.ov-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 3.5px 0;
  border-bottom: 1px dashed var(--line-soft);
}

.ov-row > span:first-child {
  color: var(--muted);
}

.ov-row.plain {
  border-bottom: 0;
}

.ov-row.plain > span:first-child {
  color: var(--rise);
}

.stack-bar {
  display: flex;
  height: 8px;
  margin: 8px 0 4px;
  border-radius: 4px;
  overflow: hidden;
}

.stack-bar i {
  display: block;
  height: 100%;
}

@media (max-width: 900px) {
  .index-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .overview {
    padding-left: 0;
    border-left: 0;
  }
}
</style>
