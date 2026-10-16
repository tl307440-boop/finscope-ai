<script setup lang="ts">
/**
 * 迷你环形图。效果图里的「重点公司跟踪」「相关公司数量」「同步任务状态」都是它。
 * segments 的 ratio 之和不必等于 1，组件按总和归一化。
 */
import { computed } from "vue"

const props = withDefaults(
  defineProps<{
    segments: readonly { label: string; ratio: number; tone?: "up" | "down" | "accent" | "ok" | "warn" | "muted" | "danger" }[]
    size?: number
    thickness?: number
    /** 环心里的主文案（例如「1,426 总任务数」） */
    centerText?: string
    centerCaption?: string
    /** 右侧图例显示百分比 */
    showLegend?: boolean
  }>(),
  { size: 96, thickness: 10, centerText: "", centerCaption: "", showLegend: false },
)

const palette: Record<string, string> = {
  up: "var(--rise)",
  down: "var(--fall)",
  accent: "var(--accent)",
  ok: "var(--ok)",
  warn: "var(--warn)",
  danger: "var(--danger)",
  muted: "var(--muted)",
}

const total = computed<number>(() => props.segments.reduce((sum, item) => sum + Math.max(0, item.ratio), 0) || 1)

const radius = computed<number>(() => (props.size - props.thickness) / 2)
const circumference = computed<number>(() => 2 * Math.PI * radius.value)

/** 每段一个 stroke-dasharray，用负的 dashoffset 把它推到上一段末尾。 */
const arcs = computed(() => {
  let consumed = 0
  return props.segments.map((item) => {
    const share = Math.max(0, item.ratio) / total.value
    const length = share * circumference.value
    const arc = {
      label: item.label,
      color: palette[item.tone ?? "accent"],
      dash: `${length} ${circumference.value - length}`,
      offset: -consumed,
      percent: `${(share * 100).toFixed(1)}%`,
    }
    consumed += length
    return arc
  })
})
</script>

<template>
  <div class="ring-wrap">
    <svg :width="props.size" :height="props.size" :viewBox="`0 0 ${props.size} ${props.size}`" aria-hidden="true" focusable="false">
      <circle
        :cx="props.size / 2"
        :cy="props.size / 2"
        :r="radius"
        fill="none"
        stroke="var(--line)"
        :stroke-width="props.thickness"
      />
      <circle
        v-for="(arc, index) in arcs"
        :key="index"
        :cx="props.size / 2"
        :cy="props.size / 2"
        :r="radius"
        fill="none"
        :stroke="arc.color"
        :stroke-width="props.thickness"
        :stroke-dasharray="arc.dash"
        :stroke-dashoffset="arc.offset"
        :transform="`rotate(-90 ${props.size / 2} ${props.size / 2})`"
      />
      <text
        v-if="props.centerText"
        :x="props.size / 2"
        :y="props.size / 2 - (props.centerCaption ? 4 : -5)"
        text-anchor="middle"
        fill="var(--text)"
        font-size="18"
        font-weight="700"
      >
        {{ props.centerText }}
      </text>
      <text
        v-if="props.centerCaption"
        :x="props.size / 2"
        :y="props.size / 2 + 14"
        text-anchor="middle"
        fill="var(--muted)"
        font-size="11"
      >
        {{ props.centerCaption }}
      </text>
    </svg>
    <ul v-if="props.showLegend" class="legend">
      <li v-for="(arc, index) in arcs" :key="index">
        <i :style="{ background: arc.color }" aria-hidden="true" />
        <span class="label">{{ arc.label }}</span>
        <span class="percent">{{ arc.percent }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ring-wrap {
  display: flex;
  align-items: center;
  gap: 14px;
}

.legend {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
  font-size: 12px;
}

.legend li {
  display: grid;
  grid-template-columns: 8px 1fr auto;
  align-items: center;
  gap: 6px;
}

.legend i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.label {
  color: var(--muted);
}

.percent {
  color: var(--text);
  font-variant-numeric: tabular-nums;
}
</style>
