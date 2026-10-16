<script setup lang="ts">
/**
 * 研究记录正文的统一呈现：报告中心的右侧预览与 /reports/:runId 单页共用。
 * 正文由 parseReportBody 拆成口径、报告期、指标与来源，这里只排版，不推算变化率。
 */
import { computed } from "vue"
import { chartableMetrics, headlineMetrics, parseReportBody, sideText } from "../../api/reportBody"
import { conceptAmount } from "../../api/secConcepts"
import MetricTile from "../../components/ui/MetricTile.vue"
import StateBlock from "../../components/ui/StateBlock.vue"
import StatusPill from "../../components/ui/StatusPill.vue"

const props = withDefaults(
  defineProps<{
    title: string
    body: string
    status: string
    asOf?: string
    modelAvailable?: boolean | null
  }>(),
  { asOf: "", modelAvailable: null },
)

const parsed = computed(() => parseReportBody(props.body))
const company = computed(() => props.title.replace(/\s*研究记录\s*$/, "") || "公司")
const headline = computed(() => headlineMetrics(parsed.value.metrics))
const chartRows = computed(() => chartableMetrics(parsed.value.metrics))
const statusText = computed(() =>
  props.status === "succeeded" ? "整理完成" : props.status === "failed" ? "整理失败" : "处理中",
)
const statusTone = computed<"ok" | "danger" | "muted">(() =>
  props.status === "succeeded" ? "ok" : props.status === "failed" ? "danger" : "muted",
)

function amountText(value: number | null | undefined, unit: string): string {
  return conceptAmount(value ?? null, unit).text
}

function amountUnit(value: number | null | undefined, unit: string): string {
  return conceptAmount(value ?? null, unit).unit
}

const chartOption = computed(() => {
  const rows = chartRows.value
  if (rows.length < 2) {
    return null
  }
  const unit = rows[0]?.unit === "CNY" ? "亿元" : "亿美元"
  const toYi = (value: number | null | undefined): number | null =>
    value == null ? null : Math.round((value / 1e8) * 100) / 100
  return {
    grid: { left: 56, right: 12, top: 36, bottom: 48 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value: number) => `${value.toLocaleString("zh-CN")} ${unit}`,
    },
    legend: { top: 0, right: 0 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      axisLabel: { interval: 0, rotate: rows.length > 5 ? 24 : 0 },
    },
    yAxis: { type: "value", name: unit },
    series: [
      {
        name: parsed.value.periods?.earlier ?? "早期",
        type: "bar",
        barMaxWidth: 18,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
        data: rows.map((row) => toYi(row.earlier?.value)),
      },
      {
        name: parsed.value.periods?.later ?? "后期",
        type: "bar",
        barMaxWidth: 18,
        itemStyle: { borderRadius: [3, 3, 0, 0] },
        data: rows.map((row) => toYi(row.later?.value)),
      },
    ],
  }
})
</script>

<template>
  <article class="report">
    <header class="cover">
      <h2>{{ title || "研究记录" }}</h2>
      <p class="tags">
        <StatusPill text="公司报告" tone="accent" />
        <StatusPill :text="company" tone="muted" />
        <StatusPill v-if="modelAvailable === false" text="规则整理" tone="warn" />
        <StatusPill v-else-if="modelAvailable" text="模型辅助" tone="accent" />
      </p>
      <p class="meta muted tabular">
        <span>FinScope 研究引擎</span>
        <span v-if="asOf">数据日期 {{ asOf }}</span>
        <span v-if="parsed.periods">报告期 {{ parsed.periods.earlier }} → {{ parsed.periods.later }}</span>
        <span>{{ parsed.metrics.length }} 项指标 · {{ parsed.sources.length }} 份申报</span>
        <StatusPill :text="statusText" :tone="statusTone" dot />
      </p>
    </header>

    <StateBlock v-if="!body" state="empty" message="这份记录没有保存正文，重新整理一次即可生成" />

    <template v-else>
      <section v-if="headline.length" class="highlights">
        <h3 class="block-title">核心观点</h3>
        <div class="grid-4 tiles">
          <MetricTile
            v-for="metric in headline"
            :key="metric.key"
            :label="metric.label"
            :value="amountText(metric.later?.value, metric.unit)"
            :unit="amountUnit(metric.later?.value, metric.unit)"
            :period="parsed.periods?.later ?? ''"
            :delta-label="parsed.periods ? `${parsed.periods.earlier}：` : '早期：'"
          >
            <template #delta>
              <span class="tabular earlier">{{ sideText(metric.earlier, metric.unit) }}</span>
            </template>
          </MetricTile>
        </div>
      </section>

      <section v-if="parsed.notes.length" class="block">
        <h3 class="block-title">一、报告说明</h3>
        <ul class="doc-list">
          <li v-for="note in parsed.notes" :key="note">{{ note }}</li>
        </ul>
      </section>

      <section class="block">
        <h3 class="block-title">二、指标对比</h3>
        <StateBlock
          v-if="!parsed.metrics.length"
          state="empty"
          message="正文里没有可逐条核对的财报数字"
        />
        <template v-else>
          <VChart v-if="chartOption" class="report-chart" :option="chartOption" autoresize />
          <el-table :data="parsed.metrics" size="small" class="metric-table">
            <el-table-column label="指标" min-width="120">
              <template #default="{ row }">{{ row.label }}</template>
            </el-table-column>
            <el-table-column :label="parsed.periods?.earlier ?? '早期'" min-width="130" align="right">
              <template #default="{ row }">
                <span class="tabular">{{ sideText(row.earlier, row.unit) }}</span>
              </template>
            </el-table-column>
            <el-table-column :label="parsed.periods?.later ?? '后期'" min-width="130" align="right">
              <template #default="{ row }">
                <span class="tabular strong">{{ sideText(row.later, row.unit) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="变化" width="90" align="right">
              <template #default="{ row }">
                <span v-if="row.change" class="tabular">{{ row.change }}</span>
                <span v-else class="muted">未计算</span>
              </template>
            </el-table-column>
            <el-table-column label="来源" width="74" align="right">
              <template #default="{ row }">
                <a
                  v-if="row.later?.sourceUrl || row.earlier?.sourceUrl"
                  :href="row.later?.sourceUrl || row.earlier?.sourceUrl"
                  target="_blank"
                  rel="noreferrer noopener"
                >原文</a>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
          <p class="muted fine">两期变化需要口径完全一致的申报才计算，本记录未给出变化值。</p>
        </template>
      </section>

      <section class="block">
        <h3 class="block-title">三、数据来源</h3>
        <p v-if="!parsed.sources.length" class="muted">没有可核对的申报来源</p>
        <ul v-else class="sources">
          <li v-for="source in parsed.sources" :key="source.accession">
            <span class="tabular">申报号 {{ source.accession }}</span>
            <a :href="source.sourceUrl" target="_blank" rel="noreferrer noopener">SEC EDGAR 原文</a>
          </li>
        </ul>
      </section>

      <section class="block">
        <h3 class="block-title">四、文档引用</h3>
        <ul v-if="parsed.documents.length" class="doc-list">
          <li v-for="line in parsed.documents" :key="line">{{ line }}</li>
        </ul>
        <p v-else class="muted">{{ parsed.documentNote || "没有引用上传文档" }}</p>
      </section>

      <section v-if="parsed.extra.length" class="block">
        <h3 class="block-title">五、补充结论</h3>
        <ul class="doc-list">
          <li v-for="line in parsed.extra" :key="line">{{ line }}</li>
        </ul>
      </section>
    </template>
  </article>
</template>

<style scoped>
.report {
  display: grid;
  gap: 16px;
}

.cover {
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background:
    radial-gradient(420px 160px at 100% 0, color-mix(in srgb, var(--accent) 22%, transparent), transparent 70%),
    linear-gradient(120deg, var(--panel-raise), var(--panel-deep));
}

.cover h2 {
  margin: 0 0 8px;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-strong);
}

.tags,
.meta {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
}

.tags {
  gap: 6px;
  margin-bottom: 10px;
}

.block-title {
  position: relative;
  margin: 0 0 10px;
  padding-left: 10px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-strong);
}

.block-title::before {
  content: "";
  position: absolute;
  left: 0;
  top: 3px;
  bottom: 3px;
  width: 3px;
  border-radius: 2px;
  background: var(--accent);
}

.tiles {
  gap: 10px;
}

.earlier {
  color: var(--text);
}

.report-chart {
  width: 100%;
  height: 250px;
  margin-bottom: 8px;
}

.strong {
  color: var(--text-strong);
  font-weight: 600;
}

.fine {
  margin: 8px 0 0;
}

.sources {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
  font-size: 13px;
}

.sources li {
  display: flex;
  gap: 12px;
  align-items: center;
}

@media (max-width: 1500px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
