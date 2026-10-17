<script setup lang="ts">
/** 涨停情绪：GET /api/v1/industry-market/limit-up?date=，东财涨停 / 炸板 / 跌停股池。 */
import { computed, onMounted, ref } from "vue"
import { companySymbol, sectionBadge } from "../../../api/industryMarket"
import { fetchLimitUp, type LimitUpView, type PoolStock } from "../../../api/marketPulse"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StatCard from "../../../components/ui/StatCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { cssVar, pctText, yuan } from "../industry/chartKit"

type PoolTab = "up" | "broken" | "down"

const POOL_TABS: { key: PoolTab; label: string }[] = [
  { key: "up", label: "涨停" },
  { key: "broken", label: "炸板" },
  { key: "down", label: "跌停" },
]

const day = ref("")
const view = ref<LimitUpView | null>(null)
const failure = ref("")
const loading = ref(false)
const tab = ref<PoolTab>("up")

const badge = computed(() => (view.value ? sectionBadge(view.value.section) : null))
const rows = computed<PoolStock[]>(() => {
  if (!view.value) {
    return []
  }
  return tab.value === "up" ? view.value.limitUpStocks : tab.value === "broken" ? view.value.brokenStocks : view.value.limitDownStocks
})
const ladder = computed(() => (view.value?.ladder ?? []).filter((step) => step.streak >= 2))
const firstBoard = computed(() => view.value?.ladder.find((step) => step.streak === 1)?.stocks.length ?? 0)

function barOption(items: { label: string; count: number }[], color: string, horizontal: boolean) {
  const labels = items.map((item) => item.label)
  const values = items.map((item) => item.count)
  const category = { type: "category", data: horizontal ? [...labels].reverse() : labels, axisLabel: { interval: 0 } }
  const value = { type: "value", minInterval: 1 }
  return {
    grid: horizontal ? { left: 84, right: 28, top: 8, bottom: 24 } : { left: 36, right: 12, top: 16, bottom: 40 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: horizontal ? value : { ...category, axisLabel: { interval: 0, rotate: 30 } },
    yAxis: horizontal ? category : value,
    series: [
      {
        type: "bar",
        data: horizontal ? [...values].reverse() : values,
        barMaxWidth: 18,
        itemStyle: { color, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
        label: { show: true, position: horizontal ? "right" : "top", color: cssVar("--muted", "#8a9bb8"), fontSize: 11 },
      },
    ],
  }
}

const industryOption = computed(() => barOption((view.value?.industries ?? []).slice(0, 15), cssVar("--rise", "#f6465d"), true))
const sealOption = computed(() => barOption(view.value?.sealTimes ?? [], "#fbbf24", false))

function link(stock: PoolStock): string {
  const symbol = companySymbol(stock.code, stock.exchange)
  return symbol ? `/company/${symbol}` : ""
}

function disabledDate(date: Date): boolean {
  return date.getTime() > Date.now()
}

async function load(): Promise<void> {
  loading.value = true
  failure.value = ""
  try {
    view.value = await fetchLimitUp(day.value)
  } catch (error) {
    failure.value = error instanceof Error ? error.message : "涨停股池暂时不可用"
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="stack">
    <PanelCard title="涨停情绪" info="东财涨停 / 炸板 / 跌停股池。封板率 = 涨停 ÷（涨停 + 炸板）；连板高度和封板时间反映短线资金的接力意愿。">
      <template #actions>
        <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
        <el-date-picker
          v-model="day"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="最近交易日"
          size="small"
          :disabled-date="disabledDate"
          clearable
          style="width: 150px"
          @change="load"
        />
      </template>
      <StateBlock v-if="loading && !view" state="loading" :rows="3" />
      <StateBlock v-else-if="failure && !view" state="error" :message="failure" @retry="load" />
      <template v-else-if="view">
        <p v-if="view.section.status !== 'ok' && view.section.reason" class="notice">{{ view.section.reason }}</p>
        <p v-if="failure" class="notice danger">{{ failure }}（下方为上次成功加载的数据）</p>
        <div class="grid-5">
          <StatCard title="涨停" :value="view.limitUp" unit="只" :caption="view.tradeDate ? `交易日 ${view.tradeDate}` : '暂无交易日数据'" tone="danger" />
          <StatCard title="炸板" :value="view.broken" unit="只" caption="盘中触及涨停后打开" tone="warn" />
          <StatCard title="跌停" :value="view.limitDown" unit="只" caption="收盘仍在跌停" tone="ok" />
          <StatCard
            title="封板率"
            :value="view.sealRate === null ? null : `${view.sealRate.toFixed(1)}%`"
            caption="涨停 ÷（涨停 + 炸板）"
            tone="accent"
          />
          <StatCard title="最高连板" :value="view.maxStreak" unit="板" :caption="`首板 ${firstBoard} 只`" tone="violet" />
        </div>
      </template>
    </PanelCard>

    <template v-if="view && view.limitUp">
      <PanelCard title="连板梯队" info="按连续涨停天数分组；同一高度内按首次封板时间排序，越早封板越强">
        <p v-if="!ladder.length" class="muted fine">今天没有 2 连板及以上的个股。</p>
        <div v-else class="ladder">
          <section v-for="step in ladder" :key="step.streak">
            <h4><b>{{ step.streak }}</b> 板 <small class="muted">{{ step.stocks.length }} 只</small></h4>
            <ul>
              <li v-for="stock in step.stocks" :key="stock.code">
                <RouterLink v-if="link(stock)" :to="link(stock)">{{ stock.name }}</RouterLink>
                <span v-else>{{ stock.name }}</span>
                <small class="muted">{{ stock.industry || "—" }} · {{ stock.firstSeal || "—" }}</small>
              </li>
            </ul>
          </section>
        </div>
      </PanelCard>

      <div class="grid-2">
        <PanelCard title="涨停行业分布" info="东财股池给出的所属行业（名称可能被截断），取前 15">
          <VChart class="chart" :option="industryOption" autoresize />
        </PanelCard>
        <PanelCard title="首次封板时间" info="越多个股在早盘封板，说明资金越坚决；尾盘封板通常更弱">
          <VChart class="chart" :option="sealOption" autoresize />
        </PanelCard>
      </div>
    </template>

    <PanelCard v-if="view" title="股池明细" flush>
      <template #actions>
        <el-radio-group v-model="tab" size="small">
          <el-radio-button v-for="item in POOL_TABS" :key="item.key" :value="item.key">{{ item.label }}</el-radio-button>
        </el-radio-group>
      </template>
      <StateBlock v-if="!rows.length" state="empty" message="这个股池今天没有个股，或数据源暂不可用" />
      <el-table v-else :data="rows" size="small" max-height="520" style="width: 100%">
        <el-table-column label="名称" min-width="110" fixed>
          <template #default="{ row }">
            <RouterLink v-if="link(row)" :to="link(row)">{{ row.name }}</RouterLink>
            <span v-else>{{ row.name }}</span>
            <small class="muted tabular code">{{ row.code }}</small>
          </template>
        </el-table-column>
        <el-table-column label="涨跌幅" width="90" align="right">
          <template #default="{ row }">
            <span class="tabular" :class="(row.changePct ?? 0) >= 0 ? 'up' : 'down'">{{ pctText(row.changePct) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最新价" width="80" align="right">
          <template #default="{ row }"><span class="tabular">{{ row.price?.toFixed(2) ?? "—" }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab === 'up'" label="连板" width="70" align="right">
          <template #default="{ row }"><span class="tabular">{{ row.streak ?? "—" }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab !== 'down'" label="首封" width="80">
          <template #default="{ row }"><span class="tabular">{{ row.firstSeal || "—" }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab === 'up'" label="末封" width="80">
          <template #default="{ row }"><span class="tabular">{{ row.lastSeal || "—" }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab !== 'broken'" label="封单资金" width="100" align="right">
          <template #default="{ row }"><span class="tabular">{{ yuan(row.sealFund) }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab !== 'down'" label="炸板次数" width="80" align="right">
          <template #default="{ row }"><span class="tabular">{{ row.breaks ?? "—" }}</span></template>
        </el-table-column>
        <el-table-column label="成交额" width="100" align="right">
          <template #default="{ row }"><span class="tabular">{{ yuan(row.amount) }}</span></template>
        </el-table-column>
        <el-table-column label="流通市值" width="100" align="right">
          <template #default="{ row }"><span class="tabular">{{ yuan(row.floatCap) }}</span></template>
        </el-table-column>
        <el-table-column label="换手" width="80" align="right">
          <template #default="{ row }"><span class="tabular">{{ row.turnoverPct === null ? "—" : `${row.turnoverPct.toFixed(2)}%` }}</span></template>
        </el-table-column>
        <el-table-column v-if="tab === 'up'" label="涨停统计" width="90">
          <template #default="{ row }">
            <span class="tabular">{{ row.limitDays && row.limitTimes ? `${row.limitDays}天${row.limitTimes}板` : "—" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="行业" min-width="100">
          <template #default="{ row }">{{ row.industry || "—" }}</template>
        </el-table-column>
      </el-table>
      <ul class="rules muted">
        <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
      </ul>
    </PanelCard>
  </div>
</template>

<style scoped>
.grid-5 {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.notice {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--warn);
}

.notice.danger {
  color: var(--danger);
}

.ladder {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 4px;
}

.ladder section {
  flex: 0 0 180px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  background: var(--panel-raise);
}

.ladder h4 {
  margin: 0 0 8px;
  font-size: 13px;
}

.ladder h4 b {
  color: var(--rise);
  font-size: 18px;
}

.ladder ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
  max-height: 260px;
  overflow: auto;
}

.ladder li {
  display: grid;
  font-size: 13px;
}

.ladder li small {
  font-size: 11px;
}

.chart {
  width: 100%;
  height: 320px;
}

.code {
  margin-left: 6px;
}

.up {
  color: var(--rise);
}

.down {
  color: var(--fall);
}

.fine {
  margin: 0;
  font-size: 12px;
}

.rules {
  margin: 0;
  padding: 10px 16px 12px 32px;
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .grid-5 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
