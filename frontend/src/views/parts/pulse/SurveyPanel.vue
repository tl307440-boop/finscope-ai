<script setup lang="ts">
/** 机构调研：GET /api/v1/industry-market/org-surveys?days=，东财数据中心的上市公司调研公告。 */
import { computed, onMounted, ref, watch } from "vue"
import { companySymbol, sectionBadge } from "../../../api/industryMarket"
import { fetchSurveys, type SurveyStock, type SurveyView } from "../../../api/marketPulse"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StatCard from "../../../components/ui/StatCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { cssVar } from "../industry/chartKit"

const WINDOWS = [7, 30, 90]

const days = ref(30)
const view = ref<SurveyView | null>(null)
const failure = ref("")
const loading = ref(false)

const badge = computed(() => (view.value ? sectionBadge(view.value.section) : null))

const dailyOption = computed(() => ({
  grid: { left: 36, right: 12, top: 16, bottom: 28 },
  tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
  xAxis: { type: "category", data: (view.value?.daily ?? []).map((item) => item.label.slice(5)) },
  yAxis: { type: "value", minInterval: 1 },
  series: [
    {
      type: "bar",
      name: "调研公告",
      data: (view.value?.daily ?? []).map((item) => item.count),
      barMaxWidth: 16,
      itemStyle: { color: cssVar("--accent", "#3b82f6"), borderRadius: [3, 3, 0, 0] },
    },
  ],
}))

const waysOption = computed(() => ({
  tooltip: { trigger: "item", formatter: "{b}：{c} 次（{d}%）" },
  legend: { bottom: 0, type: "scroll" },
  series: [
    {
      type: "pie",
      radius: ["42%", "68%"],
      center: ["50%", "44%"],
      label: { show: false },
      data: (view.value?.ways ?? []).map((item) => ({ name: item.label, value: item.count })),
    },
  ],
}))

const industryOption = computed(() => {
  const items = (view.value?.industries ?? []).slice(0, 15).reverse()
  return {
    grid: { left: 84, right: 28, top: 8, bottom: 24 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "value", minInterval: 1 },
    yAxis: { type: "category", data: items.map((item) => item.label), axisLabel: { interval: 0 } },
    series: [
      {
        type: "bar",
        name: "被调研公司数",
        data: items.map((item) => item.count),
        barMaxWidth: 16,
        itemStyle: { color: "#a78bfa", borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: "right", color: cssVar("--muted", "#8a9bb8"), fontSize: 11 },
      },
    ],
  }
})

function link(stock: { code: string }): string {
  const exchange = stock.code.startsWith("6") ? "SSE" : /^[03]/.test(stock.code) ? "SZSE" : ""
  const symbol = companySymbol(stock.code, exchange)
  return symbol ? `/company/${symbol}` : ""
}

function waysText(stock: SurveyStock): string {
  return stock.ways.join("、") || "—"
}

async function load(): Promise<void> {
  loading.value = true
  failure.value = ""
  try {
    view.value = await fetchSurveys(days.value)
  } catch (error) {
    failure.value = error instanceof Error ? error.message : "机构调研数据暂时不可用"
  } finally {
    loading.value = false
  }
}

watch(days, () => void load())
onMounted(() => void load())
</script>

<template>
  <div class="stack">
    <PanelCard title="机构调研" info="按公告日统计上市公司披露的机构调研记录；同一次调研只计一次，机构家数取公告中的接待机构数量。">
      <template #actions>
        <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
        <el-radio-group v-model="days" size="small">
          <el-radio-button v-for="item in WINDOWS" :key="item" :value="item">近 {{ item }} 天</el-radio-button>
        </el-radio-group>
      </template>
      <StateBlock v-if="loading && !view" state="loading" :rows="3" />
      <StateBlock v-else-if="failure && !view" state="error" :message="failure" @retry="load" />
      <template v-else-if="view">
        <p v-if="failure" class="notice danger">{{ failure }}（下方为上次成功加载的数据）</p>
        <div class="grid-3">
          <StatCard title="调研公告" :value="view.events" unit="次" :caption="`${view.since} 起`" tone="accent" />
          <StatCard title="被调研公司" :value="view.stocks" unit="家" caption="窗口内至少披露一次调研" tone="cyan" />
          <StatCard title="参与机构" :value="view.institutions" unit="家次" caption="各次调研接待机构数之和，会重复计入" tone="violet" />
        </div>
      </template>
    </PanelCard>

    <template v-if="view && view.events">
      <div class="grid-2-1">
        <PanelCard title="每日调研公告数">
          <VChart class="chart" :option="dailyOption" autoresize />
        </PanelCard>
        <PanelCard title="接待方式">
          <VChart class="chart" :option="waysOption" autoresize />
        </PanelCard>
      </div>

      <div class="grid-2">
        <PanelCard title="机构扎堆的公司" info="按参与机构家次排序，家次相同再看调研次数" flush>
          <el-table :data="view.topStocks" size="small" max-height="420" style="width: 100%">
            <el-table-column label="公司" min-width="110">
              <template #default="{ row }">
                <RouterLink v-if="link(row)" :to="link(row)">{{ row.name }}</RouterLink>
                <span v-else>{{ row.name }}</span>
                <small class="muted tabular code">{{ row.code }}</small>
              </template>
            </el-table-column>
            <el-table-column label="调研次数" width="80" align="right">
              <template #default="{ row }"><span class="tabular">{{ row.events }}</span></template>
            </el-table-column>
            <el-table-column label="机构家次" width="80" align="right">
              <template #default="{ row }"><span class="tabular">{{ row.institutions }}</span></template>
            </el-table-column>
            <el-table-column label="行业" min-width="90">
              <template #default="{ row }">{{ row.industry || "—" }}</template>
            </el-table-column>
            <el-table-column label="最近" width="96">
              <template #default="{ row }"><span class="tabular">{{ row.latest }}</span></template>
            </el-table-column>
            <el-table-column label="方式" min-width="110">
              <template #default="{ row }"><span class="muted">{{ waysText(row) }}</span></template>
            </el-table-column>
          </el-table>
        </PanelCard>

        <PanelCard title="被调研公司的行业分布" info="被调研次数最多的前 80 家公司按东财所属行业归类">
          <StateBlock
            v-if="view.industrySection.status === 'unavailable'"
            state="empty"
            :message="view.industrySection.reason || '行业归属暂时查不到'"
          />
          <VChart v-else class="chart tall" :option="industryOption" autoresize />
        </PanelCard>
      </div>

      <PanelCard title="最新调研公告" flush>
        <el-table :data="view.recent" size="small" max-height="360" style="width: 100%">
          <el-table-column label="公告日" width="100">
            <template #default="{ row }"><span class="tabular">{{ row.noticeDate }}</span></template>
          </el-table-column>
          <el-table-column label="调研日" width="100">
            <template #default="{ row }"><span class="tabular">{{ row.receiveDate || "—" }}</span></template>
          </el-table-column>
          <el-table-column label="公司" min-width="120">
            <template #default="{ row }">
              <RouterLink v-if="link(row)" :to="link(row)">{{ row.name }}</RouterLink>
              <span v-else>{{ row.name }}</span>
              <small class="muted tabular code">{{ row.code }}</small>
            </template>
          </el-table-column>
          <el-table-column label="机构数" width="80" align="right">
            <template #default="{ row }"><span class="tabular">{{ row.institutions }}</span></template>
          </el-table-column>
          <el-table-column label="接待方式" min-width="120">
            <template #default="{ row }">{{ row.receiveWay || "—" }}</template>
          </el-table-column>
        </el-table>
        <ul class="rules muted">
          <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
        </ul>
      </PanelCard>
    </template>
    <PanelCard v-else-if="view" title="调研明细">
      <StateBlock state="empty" :message="view.section.reason || '这个时间窗内没有调研公告'" />
    </PanelCard>
  </div>
</template>

<style scoped>
.notice.danger {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--danger);
}

.chart {
  width: 100%;
  height: 280px;
}

.chart.tall {
  height: 400px;
}

.code {
  margin-left: 6px;
}

.rules {
  margin: 0;
  padding: 10px 16px 12px 32px;
  font-size: 12px;
  line-height: 1.7;
}
</style>
