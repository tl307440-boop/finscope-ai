<script setup lang="ts">
/**
 * 股债收益差 ERP、巴菲特指标、全市场中位数 PB。
 * 数值、通道、分位与区间判断都由后端算好；输入缺失的指标整张卡写明原因，不用估算值补。
 */
import { onBeforeUnmount, onMounted, ref } from "vue"
import { Download } from "@element-plus/icons-vue"
import { ApiError } from "../../../api/client"
import {
  fetchValuation,
  valuationCsvUrl,
  type Indicator,
  type ValuationView,
} from "../../../api/customAnalysis"
import { formatNumber } from "../../../api/json"
import { VChart } from "../../../charts"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import { buildIndicatorOption, buildIndicatorPercentileOption, hasPercentiles } from "./customCharts"
import StatTile from "./StatTile.vue"

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const view = ref<ValuationView | null>(null)
let controller: AbortController | null = null

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  phase.value = "loading"
  failure.value = ""
  try {
    view.value = await fetchValuation(controller.signal)
    phase.value = "ready"
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      return
    }
    failure.value = caught instanceof ApiError ? caught.message : "无法连接数据服务"
    phase.value = "error"
  }
}

function digits(indicator: Indicator): number {
  return indicator.code === "median_pb" ? 3 : 2
}

function zoneTone(zone: string | null): "" | "cool" | "hot" {
  if (!zone) {
    return ""
  }
  if (zone.includes("机会") || zone.includes("低估")) {
    return "cool"
  }
  if (zone.includes("警戒") || zone.includes("过热") || zone.includes("偏贵")) {
    return "hot"
  }
  return ""
}

function extraText(indicator: Indicator, key: string, suffix = "", places = 2): string {
  const value = indicator.latest?.extras[key]
  return typeof value === "number" ? `${value.toFixed(places)}${suffix}` : "—"
}

function percentileText(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)}%`
}

onMounted(() => void load())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="valuation">
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else-if="view">
      <PanelCard
        v-for="indicator in view.indicators"
        :key="indicator.code"
        :title="`${indicator.name}（${indicator.unit}）`"
        :info="indicator.definition"
        class="indicator"
      >
        <template #actions>
          <a v-if="indicator.points.length" class="csv-link" :href="valuationCsvUrl()" download>
            <el-icon><Download /></el-icon>导出汇总 CSV
          </a>
        </template>

        <StateBlock
          v-if="indicator.status === 'unavailable' || !indicator.latest"
          state="empty"
          :message="indicator.reason || '数据不足，暂时无法计算'"
        />

        <template v-else>
          <StateBlock
            v-if="indicator.status === 'stale'"
            state="stale"
            :message="indicator.reason || '数据已过期，请检查同步任务'"
            :as-of="indicator.latest.tradeDate"
          />

          <div class="tiles">
            <StatTile
              label="最新值"
              :value="formatNumber(indicator.latest.value, digits(indicator))"
              :hint="indicator.latest.tradeDate"
              :tone="indicator.latest.qualityFlag === 'ok' ? '' : 'warn'"
            />
            <StatTile
              label="5 年分位"
              :value="percentileText(indicator.latest.percentile5y)"
              :hint="indicator.percentileNote ?? '最近 5 个自然年内，不高于当前值的占比'"
            />
            <StatTile
              label="所处区间"
              :value="indicator.zone ?? '—'"
              :tone="zoneTone(indicator.zone)"
            />
            <StatTile
              v-if="indicator.code === 'erp'"
              label="盈利收益率 − 国债"
              :value="`${extraText(indicator, 'earnings_yield_pct', '%')} − ${extraText(indicator, 'yield_10y_pct', '%')}`"
              :hint="`PE-TTM ${extraText(indicator, 'pe_ttm')}`"
            />
            <StatTile
              v-else-if="indicator.code === 'buffett'"
              label="总市值 / 滚动 4 季 GDP"
              :value="`${extraText(indicator, 'total_market_cap_trillion')} / ${extraText(indicator, 'trailing_gdp_trillion')} 万亿`"
              :hint="`GDP 截至 ${indicator.latest.extras.gdp_quarter_end ?? '—'}`"
            />
            <StatTile
              v-else
              label="不剔除 ST（对照）"
              :value="extraText(indicator, 'median_without_st_filter', '', 3)"
              :hint="`样本 ${extraText(indicator, 'sample', '', 0)} / 全市场 ${extraText(indicator, 'universe', '', 0)}`"
            />
          </div>

          <p v-if="indicator.latest.qualityFlag !== 'ok'" class="flag">
            最新一天的数据有待审项（单日变化过大或输入被标记），数值已入库但请谨慎使用，详见「数据源与质量」。
          </p>
          <p
            v-if="indicator.code === 'median_pb' && indicator.latest.extras.st_basis === 'current_name'"
            class="flag"
          >
            这一天的 ST 剔除按「当前简称」近似，不是当天简称；对照线与主线的差距就是这个近似的影响上限。
          </p>

          <VChart class="chart" :option="buildIndicatorOption(indicator)" autoresize />
          <VChart
            v-if="indicator.code !== 'erp' && hasPercentiles(indicator.points)"
            class="chart small"
            :option="buildIndicatorPercentileOption(indicator)"
            autoresize
          />
          <p v-if="indicator.percentileNote" class="muted fine">{{ indicator.percentileNote }}</p>

          <ul class="notes">
            <li v-for="note in indicator.notes" :key="note">{{ note }}</li>
          </ul>

          <table v-if="indicator.inputs.length" class="inputs">
            <thead>
              <tr>
                <th>输入</th>
                <th>来源</th>
                <th>最新日期</th>
                <th>行数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="input in indicator.inputs" :key="input.name">
                <td>{{ input.name }}</td>
                <td>{{ input.source }}</td>
                <td class="tabular">{{ input.lastDate ?? "—" }}</td>
                <td class="tabular">{{ input.rows }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </PanelCard>
    </template>
  </div>
</template>

<style scoped>
.valuation {
  display: grid;
  gap: var(--gap);
}

.tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
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

.notes {
  margin: 12px 0 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.inputs {
  width: 100%;
  margin-top: 12px;
  border-collapse: collapse;
  font-size: 13px;
}

.inputs th,
.inputs td {
  padding: 6px 8px;
  text-align: left;
  border-bottom: 1px solid var(--line-soft);
}

.inputs th {
  font-weight: 500;
  color: var(--muted);
}

.csv-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}

@media (max-width: 1200px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
