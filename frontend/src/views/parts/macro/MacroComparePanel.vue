<script setup lang="ts">
/**
 * 多指标对比分析：原值（左右双轴，最多两种单位）或指数化（2020-01 = 100），可导出 CSV。
 * 数据由页面传入（已按观察日过滤），本组件不请求网络。
 */
import { computed, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../../../charts"
import {
  BASE_MONTH,
  buildCompare,
  toCsv,
  type CompareMode,
  type CompareSpec,
} from "../../../api/macroCompare"
import { macroPalette } from "../../../api/macroPalette"
import type { Observation } from "../../../api/macroTiles"
import PanelCard from "../../../components/ui/PanelCard.vue"
import SeriesPicker from "../../../components/ui/SeriesPicker.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const props = defineProps<{
  specs: readonly CompareSpec[]
  rowsByCode: Readonly<Record<string, readonly Observation[]>>
  defaultCodes: readonly string[]
  /** 导出文件名前缀，如 china-macro-compare */
  filename: string
  defaultMode?: CompareMode
}>()

const YEAR_OPTIONS = [1, 3, 5, 0] as const

const codes = ref<string[]>([...props.defaultCodes])
const mode = ref<CompareMode>(props.defaultMode ?? "raw")
const years = ref<number>(5)

const result = computed(() => buildCompare(props.rowsByCode, props.specs, codes.value, mode.value, years.value))
const hasIndexable = computed<boolean>(() => props.specs.some((spec) => spec.indexable))

const options = computed(() => {
  const colors = macroPalette()
  return props.specs.map((spec) => ({
    code: spec.code,
    label: spec.label,
    color: colors[spec.tone],
    disabled: (props.rowsByCode[spec.code]?.length ?? 0) < 2,
    hint: `${spec.label} · ${spec.unit}${spec.indexable ? " · 可指数化" : ""}`,
  }))
})

function valueTip(unit: string) {
  return (value: unknown): string => (typeof value === "number" ? `${value.toFixed(2)}${unit === "%" ? "%" : ` ${unit}`}` : "—")
}

const option = computed<EChartsCoreOption | null>(() => {
  const current = result.value
  if (!current.lines.length) {
    return null
  }
  const colors = macroPalette()
  const dual = current.mode === "raw" && current.axisUnits.length > 1
  const axis = (index: number) => {
    const unit = current.axisUnits[index] ?? ""
    return {
      type: "value",
      scale: true,
      position: index === 0 ? "left" : "right",
      axisLabel: { color: colors.muted, formatter: unit === "%" ? "{value}%" : "{value}" },
      name: current.mode === "index" ? `指数（${BASE_MONTH}=100）` : unit,
      nameTextStyle: { color: colors.muted },
      splitLine: { show: index === 0, lineStyle: { color: colors.line } },
    }
  }
  return {
    grid: { left: 8, right: dual ? 8 : 12, top: 28, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: colors.muted }, type: "scroll" },
    xAxis: { type: "time", axisLabel: { hideOverlap: true } },
    yAxis: dual ? [axis(0), axis(1)] : [axis(0)],
    series: current.lines.map((line, position) => {
      const color = colors[line.spec.tone]
      return {
        name: line.spec.label,
        type: "line",
        yAxisIndex: line.axis,
        showSymbol: line.rows.length < 40,
        connectNulls: true,
        data: line.rows.map((row) => [row.date, row.value]),
        tooltip: { valueFormatter: valueTip(current.mode === "index" ? "" : line.spec.unit) },
        lineStyle: { color, width: 1.7 },
        itemStyle: { color },
        markLine:
          current.mode === "index" && position === 0
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { color: colors.muted, type: "dashed" },
                label: { color: colors.muted, fontSize: 11, formatter: `基期 ${BASE_MONTH}=100` },
                data: [{ yAxis: 100 }],
              }
            : undefined,
      }
    }),
  }
})

function exportCsv(): void {
  const text = `\ufeff${toCsv(result.value)}`
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = `${props.filename}-${mode.value}.csv`
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <PanelCard
    title="宏观数据对比分析"
    :info="`原值：最多两种单位，左右各一根轴，不同频率按时间轴对齐、不插值。指数化：把${BASE_MONTH}当月的值定为 100，只对价格指数、就业人数、GDP 水平值等水平值有意义`"
  >
    <template #icon><span class="panel-icon">⇄</span></template>
    <template #actions>
      <div class="mode" role="group" aria-label="对比视图">
        <button type="button" :class="{ active: mode === 'raw' }" :aria-pressed="mode === 'raw'" @click="mode = 'raw'">
          原值
        </button>
        <button
          type="button"
          :class="{ active: mode === 'index' }"
          :aria-pressed="mode === 'index'"
          :disabled="!hasIndexable"
          :title="hasIndexable ? '' : '本页没有可指数化的水平值序列'"
          @click="mode = 'index'"
        >
          指数化
        </button>
      </div>
      <button type="button" class="export" :disabled="!result.lines.length" @click="exportCsv">导出 CSV</button>
    </template>

    <SeriesPicker
      v-model="codes"
      v-model:years="years"
      :options="options"
      :year-options="mode === 'raw' ? YEAR_OPTIONS : []"
    />
    <VChart v-if="option" class="chart compare-chart" :option="option" autoresize />
    <StateBlock
      v-else
      state="empty"
      :message="codes.length ? '所选指标在当前视图下没有可画的数据，原因见下方说明' : '请在上方至少选择一个指标'"
    />
    <ul v-if="result.skipped.length" class="skipped">
      <li v-for="item in result.skipped" :key="item.code">{{ item.label }}：{{ item.reason }}</li>
    </ul>
    <p class="note">
      {{
        mode === "index"
          ? `指数化 = 当前值 ÷ ${BASE_MONTH} 当月值 × 100，从基期画到最新；不同频率各画各的观测点。`
          : "原值视图：不同频率各画各的观测点；导出的 CSV 在没有观测的日期留空。"
      }}
    </p>
  </PanelCard>
</template>

<style scoped>
.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.compare-chart {
  width: 100%;
  height: 300px;
}

.mode {
  display: inline-flex;
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
}

.mode button,
.export {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  line-height: 22px;
  padding: 0 10px;
  cursor: pointer;
}

.mode button.active {
  background: color-mix(in srgb, var(--accent) 22%, transparent);
  color: var(--text);
}

.mode button:disabled,
.export:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.export {
  margin-left: 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
}

.skipped {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--warn);
  font-size: 12px;
  line-height: 1.7;
}

.note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}
</style>
