<script setup lang="ts">
/**
 * 自选看板：某个分组的公开行情、所属行业、主力资金、涨停/调研/热榜信号。
 * 涨跌、加权、行业暴露都由后端计算；这里只排序、着色与绘图。
 */
import { computed, ref, watch } from "vue"
import { FetchFailure, sectionBadge } from "../../../api/industryMarket"
import { fetchInsights, type WatchStock, type WatchlistInsights } from "../../../api/watchlistInsights"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StatCard from "../../../components/ui/StatCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { restoreSession, useSessionStore } from "../../../stores/auth"
import { cssVar, heatColor, pctText, shortTime, toYi, yuan } from "../industry/chartKit"

const props = defineProps<{ groupId: string; version: number }>()

const session = useSessionStore()
const view = ref<WatchlistInsights | null>(null)
const failure = ref("")
const loading = ref(false)

const quoteBadge = computed(() => (view.value ? sectionBadge(view.value.quoteSection) : null))
const summary = computed(() => view.value?.summary ?? null)
const stocks = computed<WatchStock[]>(() =>
  [...(view.value?.stocks ?? [])].sort((a, b) => (b.changePct ?? -Infinity) - (a.changePct ?? -Infinity)),
)

const exposureOption = computed(() => {
  const text = cssVar("--text-strong", "#f1f5ff")
  return {
    tooltip: {
      formatter: (info: { data?: { name?: string; count?: number; weight?: number | null; pct?: number | null; cap?: number } }) => {
        const data = info.data
        if (!data?.name) {
          return ""
        }
        return [
          `<b>${data.name}</b>`,
          `${data.count} 只 · 市值占比 ${data.weight === null || data.weight === undefined ? "—" : `${data.weight.toFixed(1)}%`}`,
          `市值加权今日涨跌 ${pctText(data.pct ?? null)}`,
          `合计市值 ${yuan(data.cap ?? null)}元`,
        ].join("<br/>")
      },
    },
    series: [
      {
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        label: {
          color: text,
          fontSize: 12,
          formatter: (info: { data?: { name?: string; pct?: number | null } }) =>
            `${info.data?.name ?? ""}\n${pctText(info.data?.pct ?? null)}`,
        },
        itemStyle: { borderColor: cssVar("--panel", "#0f1b31"), borderWidth: 2, gapWidth: 2 },
        data: (view.value?.industries ?? []).map((item) => ({
          name: item.name,
          value: item.marketCap && item.marketCap > 0 ? item.marketCap : item.count,
          count: item.count,
          weight: item.weightPct,
          pct: item.weightedChangePct,
          cap: item.marketCap,
          itemStyle: { color: heatColor(item.weightedChangePct, 4) },
        })),
      },
    ],
  }
})

const momentumRows = computed(() =>
  stocks.value.filter((item) => item.change60dPct !== null && item.change5dPct !== null),
)
const momentumCount = computed(() => momentumRows.value.length)

const scatterOption = computed(() => {
  const rows = momentumRows.value
  const caps = rows.map((item) => item.marketCap ?? 0)
  const maxCap = Math.max(1, ...caps)
  return {
    grid: { left: 52, right: 20, top: 20, bottom: 44 },
    tooltip: {
      formatter: (info: { data?: { name?: string; value?: number[]; cap?: number | null } }) => {
        const data = info.data
        if (!data?.value) {
          return ""
        }
        return `<b>${data.name}</b><br/>60日 ${pctText(data.value[0] ?? null)} · 5日 ${pctText(data.value[1] ?? null)}<br/>市值 ${yuan(data.cap ?? null)}元`
      },
    },
    xAxis: { type: "value", name: "60日涨跌 %", nameLocation: "middle", nameGap: 28, splitLine: { show: false } },
    yAxis: { type: "value", name: "5日涨跌 %", nameLocation: "middle", nameGap: 36, splitLine: { show: false } },
    series: [
      {
        type: "scatter",
        data: rows.map((item) => ({
          name: item.name,
          value: [item.change60dPct, item.change5dPct],
          cap: item.marketCap,
          symbolSize: 8 + Math.sqrt((item.marketCap ?? 0) / maxCap) * 26,
          itemStyle: { color: heatColor(item.changePct, 4), borderColor: "rgba(255,255,255,0.35)" },
        })),
        label: { show: true, position: "right", fontSize: 11, color: cssVar("--muted", "#8a9bb8"), formatter: "{b}" },
        labelLayout: { hideOverlap: true },
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: "rgba(148,163,184,0.45)", type: "dashed" },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
        },
      },
    ],
  }
})

function companyPath(stock: WatchStock): string {
  return `/company/${encodeURIComponent(stock.displaySymbol)}`
}

function signals(stock: WatchStock): { text: string; tone: "danger" | "warn" | "ok" | "accent" | "muted" }[] {
  const items: { text: string; tone: "danger" | "warn" | "ok" | "accent" | "muted" }[] = []
  if (stock.limitStatus === "涨停") {
    items.push({ text: stock.streak && stock.streak > 1 ? `${stock.streak}连板` : "涨停", tone: "danger" })
  } else if (stock.limitStatus === "炸板") {
    items.push({ text: "炸板", tone: "warn" })
  } else if (stock.limitStatus === "跌停") {
    items.push({ text: "跌停", tone: "ok" })
  }
  if (stock.surveys) {
    items.push({ text: `30日调研 ${stock.surveys} 次`, tone: "accent" })
  }
  if (stock.thsHotRank) {
    items.push({ text: `同花顺热股 #${stock.thsHotRank}`, tone: "muted" })
  }
  if (stock.emHotRank) {
    items.push({ text: `东财人气 #${stock.emHotRank}`, tone: "muted" })
  }
  return items
}

async function refreshToken(): Promise<string | null> {
  return (await restoreSession()) ? session.accessToken : null
}

async function load(): Promise<void> {
  if (!props.groupId) {
    view.value = null
    return
  }
  loading.value = true
  failure.value = ""
  try {
    view.value = await fetchInsights(props.groupId, session.accessToken, refreshToken)
  } catch (error) {
    failure.value =
      error instanceof FetchFailure && error.status === 401
        ? "登录已失效，请重新登录"
        : error instanceof Error
          ? error.message
          : "自选看板暂时不可用"
  } finally {
    loading.value = false
  }
}

watch(() => [props.groupId, props.version], () => void load(), { immediate: true })
</script>

<template>
  <div class="stack">
    <PanelCard title="自选看板" info="自选里只保存证券代码；行情、所属行业、主力资金、涨停、机构调研和热榜都从东财 / 同花顺公开数据现查">
      <template #actions>
        <StatusPill v-if="quoteBadge" :text="`行情 ${quoteBadge.text}`" :tone="quoteBadge.tone" dot />
        <StatusPill
          v-if="view"
          :text="view.signalSection.status === 'ok' ? '信号齐全' : '部分信号缺失'"
          :tone="view.signalSection.status === 'ok' ? 'ok' : 'warn'"
          dot
        />
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </template>
      <StateBlock v-if="loading && !view" state="loading" :rows="3" />
      <StateBlock v-else-if="failure && !view" state="error" :message="failure" @retry="load" />
      <StateBlock v-else-if="view && !view.stocks.length" state="empty" message="这个分组还没有 A 股标的，可在上方「批量导入」粘贴代码" />
      <template v-else-if="view && summary">
        <p v-if="failure" class="notice danger">{{ failure }}（下方为上次成功加载的数据）</p>
        <p v-if="view.quoteSection.status !== 'ok' && view.quoteSection.reason" class="notice">{{ view.quoteSection.reason }}</p>
        <p v-if="view.signalSection.reason" class="notice">{{ view.signalSection.reason }}</p>
        <div class="kpis">
          <StatCard
            title="涨 / 跌"
            :value="`${summary.up} / ${summary.down}`"
            :caption="`${summary.quoted} / ${summary.count} 只有报价，平 ${summary.flat}`"
            tone="accent"
          />
          <StatCard
            title="等权平均涨跌"
            :value="summary.averageChangePct === null ? null : pctText(summary.averageChangePct)"
            caption="各股今日涨跌幅的简单平均"
            tone="cyan"
          />
          <StatCard
            title="市值加权涨跌"
            :value="summary.weightedChangePct === null ? null : pctText(summary.weightedChangePct)"
            caption="按总市值加权，更接近持仓感受"
            tone="violet"
          />
          <StatCard
            title="主力净流入"
            :value="summary.totalMainNetInflow === null ? null : yuan(summary.totalMainNetInflow)"
            unit="元"
            caption="超大单 + 大单净额合计"
            tone="orange"
          />
          <StatCard
            title="信号"
            :value="`${summary.limitUp} / ${summary.surveyed} / ${summary.hot}`"
            caption="涨停 / 30日内被调研 / 上热榜"
            tone="danger"
          />
        </div>
      </template>
    </PanelCard>

    <template v-if="view && view.stocks.length">
      <div class="grid-2">
        <PanelCard title="行业暴露" info="按东财所属行业归类；面积 = 合计总市值，颜色 = 该行业自选股的市值加权今日涨跌">
          <StateBlock v-if="!view.industries.length" state="empty" message="暂时查不到所属行业（实时行情不可用时没有行业字段）" />
          <VChart v-else class="chart" :option="exposureOption" autoresize />
        </PanelCard>
        <PanelCard title="动量分布" info="横轴 60 日、纵轴 5 日涨跌；右上角是中短期都在走强的标的，圆点大小 = 总市值，颜色 = 今日涨跌">
          <StateBlock v-if="!momentumCount" state="empty" message="还没有 5 日 / 60 日涨跌数据（实时行情不可用且本系统没有这些股票的日线）" />
          <VChart v-else class="chart" :option="scatterOption" autoresize />
        </PanelCard>
      </div>

      <PanelCard title="自选明细" flush>
        <template #actions>
          <span v-if="view.quoteSection.fetchedAt" class="muted fine">取数 {{ shortTime(view.quoteSection.fetchedAt) }}</span>
        </template>
        <el-table :data="stocks" size="small" max-height="560" style="width: 100%">
          <el-table-column label="名称" min-width="120" fixed>
            <template #default="{ row }">
              <RouterLink :to="companyPath(row)">{{ row.name }}</RouterLink>
              <small class="muted tabular code">{{ row.code }}</small>
            </template>
          </el-table-column>
          <el-table-column label="最新" width="80" align="right">
            <template #default="{ row }"><span class="tabular">{{ row.last?.toFixed(2) ?? "—" }}</span></template>
          </el-table-column>
          <el-table-column label="今日" width="84" align="right">
            <template #default="{ row }"><DeltaValue :value="row.changePct" suffix="%" size="sm" signed /></template>
          </el-table-column>
          <el-table-column label="5日" width="84" align="right">
            <template #default="{ row }"><DeltaValue :value="row.change5dPct" suffix="%" size="sm" signed /></template>
          </el-table-column>
          <el-table-column label="60日" width="84" align="right">
            <template #default="{ row }"><DeltaValue :value="row.change60dPct" suffix="%" size="sm" signed /></template>
          </el-table-column>
          <el-table-column label="年初至今" width="90" align="right">
            <template #default="{ row }"><DeltaValue :value="row.changeYtdPct" suffix="%" size="sm" signed /></template>
          </el-table-column>
          <el-table-column label="主力净流入" width="104" align="right">
            <template #default="{ row }">
              <span class="tabular" :class="(row.mainNetInflow ?? 0) >= 0 ? 'up' : 'down'">{{ yuan(row.mainNetInflow) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="成交额" width="96" align="right">
            <template #default="{ row }"><span class="tabular">{{ yuan(row.amount) }}</span></template>
          </el-table-column>
          <el-table-column label="PE(动)" width="76" align="right">
            <template #default="{ row }"><span class="tabular">{{ row.peDynamic?.toFixed(1) ?? "—" }}</span></template>
          </el-table-column>
          <el-table-column label="PB" width="64" align="right">
            <template #default="{ row }"><span class="tabular">{{ row.pb?.toFixed(2) ?? "—" }}</span></template>
          </el-table-column>
          <el-table-column label="总市值(亿)" width="92" align="right">
            <template #default="{ row }"><span class="tabular">{{ toYi(row.marketCap)?.toFixed(0) ?? "—" }}</span></template>
          </el-table-column>
          <el-table-column label="行业" min-width="96">
            <template #default="{ row }">
              <RouterLink v-if="row.boardCode" :to="`/industry/board/${row.boardCode}`">{{ row.industry }}</RouterLink>
              <span v-else>{{ row.industry || "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="信号" min-width="200">
            <template #default="{ row }">
              <div class="signals">
                <StatusPill v-for="item in signals(row)" :key="item.text" :text="item.text" :tone="item.tone" />
                <span v-if="!signals(row).length" class="muted">—</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="来源" width="76">
            <template #default="{ row }">
              <span class="muted">{{ row.last === null ? "无数据" : row.origin === "stored" ? "入库日线" : "实时" }}</span>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="view.unsupported.length" class="muted fine pad">
          {{ view.unsupported.join("、") }} 不是沪深京 A 股，看板暂不统计。
        </p>
        <ul class="rules muted">
          <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
        </ul>
      </PanelCard>
    </template>
  </div>
</template>

<style scoped>
.kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.notice {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--warn);
}

.notice.danger {
  color: var(--danger);
}

.chart {
  width: 100%;
  height: 340px;
}

.code {
  margin-left: 6px;
}

.signals {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
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

.pad {
  padding: 10px 16px 0;
}

.rules {
  margin: 0;
  padding: 10px 16px 12px 32px;
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
