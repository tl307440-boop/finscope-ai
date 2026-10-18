<script setup lang="ts">
/**
 * 宽基（上证50 / 沪深300 / 中证500 / 中证1000）与科创50 的期权 PCR。每个宽基下 ETF 期权篮子与中金所股指期权篮子分开展示（不是同一种合约，不混算）。
 * PCR、均线、分位、信号都是后端算好的；缺数据的篮子写明原因，不画空图。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { Download } from "@element-plus/icons-vue"
import { ApiError } from "../../../api/client"
import {
  PCR_GROUPS,
  fetchPcr,
  pcrCsvUrl,
  type PcrGroupCode,
  type PcrView,
} from "../../../api/customAnalysis"
import { formatNumber } from "../../../api/json"
import { VChart } from "../../../charts"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import { buildPcrOption, buildPcrPercentileOption, hasPercentiles } from "./customCharts"
import StatTile from "./StatTile.vue"

const group = ref<PcrGroupCode>("csi300")
const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const view = ref<PcrView | null>(null)
const cache = new Map<PcrGroupCode, PcrView>()
let controller: AbortController | null = null

async function load(): Promise<void> {
  const wanted = group.value
  const cached = cache.get(wanted)
  if (cached) {
    view.value = cached
    phase.value = "ready"
    return
  }
  controller?.abort()
  controller = new AbortController()
  phase.value = "loading"
  failure.value = ""
  try {
    const loaded = await fetchPcr(wanted, controller.signal)
    cache.set(wanted, loaded)
    if (group.value === wanted) {
      view.value = loaded
      phase.value = "ready"
    }
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      return
    }
    if (group.value === wanted) {
      failure.value = caught instanceof ApiError ? caught.message : "无法连接数据服务"
      phase.value = "error"
    }
  }
}

function retry(): void {
  cache.delete(group.value)
  void load()
}

function tone(signal: string | null): "" | "cool" | "hot" {
  if (!signal) {
    return ""
  }
  if (signal.includes("恐慌")) {
    return "hot"
  }
  if (signal.includes("贪婪")) {
    return "cool"
  }
  return ""
}

function percentileText(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`
}

function dayText(value: string | null): string {
  return value ?? "—"
}

const baskets = computed(() => view.value?.baskets ?? [])

onMounted(() => void load())
onBeforeUnmount(() => controller?.abort())
watch(group, () => void load())
</script>

<template>
  <div class="pcr">
    <div class="group-tabs" role="tablist" aria-label="宽基指数">
      <button
        v-for="item in PCR_GROUPS"
        :key="item.code"
        type="button"
        role="tab"
        class="group-tab"
        :class="{ active: group === item.code }"
        :aria-selected="group === item.code"
        @click="group = item.code"
      >
        {{ item.name }}
      </button>
    </div>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="retry" />

    <template v-else-if="view">
      <PanelCard
        v-for="basket in baskets"
        :key="basket.kind"
        :title="`${view.groupName} · ${basket.label}`"
        :info="basket.note"
        class="basket"
      >
        <template #actions>
          <a
            v-if="basket.points.length"
            class="csv-link"
            :href="pcrCsvUrl(group, basket.kind)"
            download
          >
            <el-icon><Download /></el-icon>导出 CSV
          </a>
        </template>

        <StateBlock
          v-if="basket.status === 'unavailable' || !basket.latest"
          state="empty"
          :message="basket.reason || '还没有可用的期权数据，请先运行数据同步'"
        />

        <template v-else>
          <StateBlock
            v-if="basket.status === 'stale'"
            state="stale"
            :message="basket.reason || '数据已过期，请检查同步任务'"
            :as-of="dayText(basket.lastDate)"
          />

          <div class="tiles">
            <StatTile
              label="成交量 PCR"
              :value="formatNumber(basket.latest.volumePcr, 3)"
              :hint="basket.latest.sentimentSignal ?? '—'"
              :tone="tone(basket.latest.sentimentSignal)"
            />
            <StatTile label="10 日均线" :value="formatNumber(basket.latest.volumePcrMa10, 3)" />
            <StatTile label="持仓量 PCR" :value="formatNumber(basket.latest.oiPcr, 3)" />
            <StatTile
              label="5 年分位"
              :value="percentileText(basket.latest.percentile5y)"
              :hint="basket.latest.relativeSignal ?? '样本不足，暂不计算'"
              :tone="tone(basket.latest.relativeSignal)"
            />
            <StatTile
              label="最新交易日"
              :value="basket.latest.tradeDate"
              :hint="`认沽 ${basket.latest.putVolume.toLocaleString('zh-CN')} / 认购 ${basket.latest.callVolume.toLocaleString('zh-CN')}`"
              :tone="basket.latest.qualityFlag === 'ok' ? '' : 'warn'"
            />
          </div>

          <p v-if="basket.latest.qualityFlag !== 'ok'" class="flag">
            最新一天的数据有待审项（与交易所公布的比值对不上），数值已入库但请谨慎使用，详见「数据源与质量」。
          </p>
          <p v-if="basket.reason && basket.status !== 'stale'" class="flag">{{ basket.reason }}</p>

          <VChart
            v-if="basket.points.length >= 2"
            class="chart"
            :option="buildPcrOption(basket.points, true)"
            autoresize
          />
          <p v-else class="muted fine">只有 {{ basket.points.length }} 个可用交易日，不足以画走势图。</p>
          <VChart
            v-if="hasPercentiles(basket.points)"
            class="chart small"
            :option="buildPcrPercentileOption(basket.points)"
            autoresize
          />
          <p v-else-if="!basket.reason" class="muted fine">
            有效样本不足 750 个交易日，暂不计算 5 年分位与相对信号。
          </p>

          <p class="muted fine">
            数据区间 {{ dayText(basket.firstDate) }} ~ {{ dayText(basket.lastDate) }}（{{
              basket.historyYears === null ? "—" : `${basket.historyYears.toFixed(1)} 年`
            }}）<template v-if="basket.incompleteDays"
              >；另有 {{ basket.incompleteDays }} 天因篮子成员缺数据未计入</template
            >。
          </p>
        </template>
      </PanelCard>

      <PanelCard title="口径说明" tone="orange">
        <ul class="rules">
          <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
        </ul>
      </PanelCard>
    </template>
  </div>
</template>

<style scoped>
.pcr {
  display: grid;
  gap: var(--gap);
}

.group-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.group-tab {
  padding: 6px 16px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}

.group-tab:hover {
  color: var(--text-strong);
}

.group-tab.active {
  color: var(--text-strong);
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}

.tiles {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.chart {
  width: 100%;
  height: 340px;
}

.chart.small {
  height: 180px;
}

.flag {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--warn);
}

.fine {
  margin: 8px 0 0;
  font-size: 12px;
}

.csv-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}

.rules {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

@media (max-width: 1200px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
