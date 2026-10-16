<script setup lang="ts">
/**
 * 对照实验：标的 × 日期网格逐点推演，事后按入库收盘价结算相对沪深300的超额。
 * 命中率、均值等统计全部由后端算好，这里只排版与画图。
 */
import { computed, reactive, ref } from "vue"
import VChart from "vue-echarts"
import { RATING_LABEL, runExperiment, signedPercent, type ExperimentResult } from "../../../api/simulation"
import StateBlock from "../../../components/ui/StateBlock.vue"

const props = defineProps<{
  token: string | null
  refresh: () => Promise<string | null>
  defaultSymbol: string
}>()

const form = reactive({
  symbols: props.defaultSymbol,
  start: "2025-01-02",
  end: "2026-06-30",
  step: 20,
  holding: 30,
})
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const message = ref("")
const result = ref<ExperimentResult | null>(null)

const symbols = computed(() =>
  form.symbols
    .split(/[\s,，、]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean),
)

async function start(): Promise<void> {
  if (!symbols.value.length) {
    phase.value = "error"
    message.value = "至少填写一个标的代码"
    return
  }
  phase.value = "loading"
  message.value = ""
  try {
    result.value = await runExperiment(
      {
        symbols: symbols.value.slice(0, 8),
        start: form.start,
        end: form.end,
        step: form.step,
        holding_days: form.holding,
      },
      props.token,
      props.refresh,
    )
    phase.value = result.value ? "ready" : "error"
    if (!result.value) {
      message.value = "接口没有返回实验结果"
    }
  } catch (caught) {
    phase.value = "error"
    message.value = caught instanceof Error ? caught.message : "无法连接接口"
  }
}

const pct = (value: number | null): number | null => (value === null ? null : Number((value * 100).toFixed(2)))

const chart = computed(() => {
  const rows = result.value?.byRating ?? []
  const label = rows.map((row) => `${RATING_LABEL[row.rating] ?? row.rating}`)
  return {
    grid: { left: 48, right: 12, top: 34, bottom: 28 },
    legend: { top: 0, right: 0 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: number | null) => (value === null ? "—" : `${value}%`),
    },
    xAxis: { type: "category", data: label },
    yAxis: { type: "value", axisLabel: { formatter: "{value}%" } },
    series: [
      {
        name: "多角色推演",
        type: "bar",
        barMaxWidth: 22,
        data: rows.map((row) => pct(row.meanExcess)),
        itemStyle: { color: "#3b82f6", borderRadius: [3, 3, 0, 0] },
      },
      {
        name: "仅技术面",
        type: "bar",
        barMaxWidth: 22,
        data: rows.map((row) => pct(row.baselineMeanExcess)),
        itemStyle: { color: "#a78bfa", borderRadius: [3, 3, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <div class="experiment">
    <form class="form" @submit.prevent="start">
      <label class="wide">
        <span>标的（逗号分隔，最多 8 个）</span>
        <input v-model="form.symbols" name="experiment-symbols" placeholder="600519.SH, 300750.SZ" />
      </label>
      <label>
        <span>起始</span>
        <input v-model="form.start" type="date" name="experiment-start" />
      </label>
      <label>
        <span>截止</span>
        <input v-model="form.end" type="date" name="experiment-end" />
      </label>
      <label>
        <span>采样间隔（交易日）</span>
        <input v-model.number="form.step" type="number" min="1" max="60" name="experiment-step" />
      </label>
      <label>
        <span>持有期（天）</span>
        <input v-model.number="form.holding" type="number" min="5" max="120" name="experiment-holding" />
      </label>
      <button type="submit" class="run" :disabled="phase === 'loading'">
        {{ phase === "loading" ? "回测中…" : "运行对照实验" }}
      </button>
    </form>

    <StateBlock v-if="phase === 'loading'" state="loading" title="逐日推演并结算中" message="每个采样日只读取当日及以前的数据" />
    <StateBlock v-else-if="phase === 'error'" state="error" title="实验没有完成" :message="message" />
    <StateBlock
      v-else-if="phase === 'ready' && result && !result.samples"
      state="empty"
      title="没有可结算的样本"
      :message="result.skipped[0] ?? '所选区间内没有走完持有期的样本'"
    />

    <template v-else-if="phase === 'ready' && result">
      <table class="modes">
        <thead>
          <tr>
            <th>方案</th>
            <th>样本</th>
            <th>方向性判断</th>
            <th>命中率</th>
            <th>看多均超额</th>
            <th>看空均超额</th>
            <th>多空价差</th>
            <th>按方向持有的平均超额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="mode in result.modes" :key="mode.code">
            <th scope="row">{{ mode.label }}</th>
            <td class="tabular">{{ mode.samples }}</td>
            <td class="tabular">{{ mode.directionalCalls }}</td>
            <td class="tabular">{{ mode.hitRate === null ? "—" : `${(mode.hitRate * 100).toFixed(1)}%` }}</td>
            <td class="tabular" :class="{ rise: (mode.longMeanExcess ?? 0) > 0, fall: (mode.longMeanExcess ?? 0) < 0 }">
              {{ signedPercent(mode.longMeanExcess) }}
            </td>
            <td class="tabular" :class="{ rise: (mode.shortMeanExcess ?? 0) > 0, fall: (mode.shortMeanExcess ?? 0) < 0 }">
              {{ signedPercent(mode.shortMeanExcess) }}
            </td>
            <td class="tabular">{{ signedPercent(mode.longShortSpread) }}</td>
            <td class="tabular">{{ signedPercent(mode.meanSignedExcess) }}</td>
          </tr>
        </tbody>
      </table>
      <VChart class="chart" :option="chart" autoresize />
      <p class="foot">
        {{ result.samples }} 个已结算样本，跳过 {{ result.skippedCount }} 个（持有期未走完或无日线）{{
          result.truncated ? "，已达单次 240 个推演上限" : ""
        }}。超额 = 区间收益 − 沪深300 同期收益；样本小，结论只供方法验证。
      </p>
    </template>
  </div>
</template>

<style scoped>
.experiment {
  display: grid;
  gap: 14px;
}

.form {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
  gap: 10px;
  align-items: end;
}

.wide {
  grid-column: 1 / -1;
}

label {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

input {
  height: 32px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-deep);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  min-width: 0;
}

input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.run {
  height: 32px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-raise);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.run:hover:not(:disabled) {
  border-color: var(--edge-light);
}

.run:disabled {
  opacity: 0.6;
  cursor: progress;
}

.modes {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.modes th,
.modes td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-soft);
  text-align: right;
  white-space: nowrap;
}

.modes thead th {
  font-weight: 400;
  font-size: 12px;
  color: var(--muted);
}

.modes th:first-child,
.modes tbody th {
  text-align: left;
  font-weight: 500;
  color: var(--text);
}

.rise {
  color: var(--rise);
}

.fall {
  color: var(--fall);
}

.chart {
  height: 220px;
}

.foot {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

@media (max-width: 1100px) {
  .form {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
