<script setup lang="ts">
/**
 * 迷你走势图。效果图里每张 KPI 卡右下角那条小线/小柱就是它。
 * 纯 SVG，不走 ECharts——主图才用 ECharts，迷你图上百个实例会拖垮页面。
 */
import { computed } from "vue"

const props = withDefaults(
  defineProps<{
    points: readonly (number | null)[]
    kind?: "line" | "area" | "bar"
    tone?: "up" | "down" | "accent" | "ok" | "warn" | "muted"
    width?: number
    height?: number
    /** 画一条水平虚线基准（例如通胀目标 2%） */
    baseline?: number | null
  }>(),
  { kind: "line", tone: "accent", width: 120, height: 40, baseline: null },
)

const values = computed<number[]>(() => props.points.filter((item): item is number => typeof item === "number"))

const color = computed<string>(() => {
  switch (props.tone) {
    case "up":
      return "var(--rise)"
    case "down":
      return "var(--fall)"
    case "ok":
      return "var(--ok)"
    case "warn":
      return "var(--warn)"
    case "muted":
      return "var(--muted)"
    default:
      return "var(--accent)"
  }
})

/** 把数据映射到 0..width / 0..height 的坐标，两端各留 2px 免得被裁掉。 */
const geometry = computed(() => {
  const list = values.value
  const pad = 2
  const usableHeight = props.height - pad * 2
  if (list.length < 2) {
    return { path: "", area: "", bars: [] as { x: number; y: number; w: number; h: number }[], baselineY: null as number | null }
  }
  const candidates = props.baseline === null ? list : [...list, props.baseline]
  const high = Math.max(...candidates)
  const low = Math.min(...candidates)
  const span = high - low || 1
  const stepX = props.width / (list.length - 1)
  const y = (value: number): number => pad + (1 - (value - low) / span) * usableHeight

  const coords = list.map((value, index) => ({ x: index * stepX, y: y(value) }))
  const path = coords.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ")
  const area = `${path} L${props.width},${props.height} L0,${props.height} Z`

  const slot = props.width / list.length
  const bars = coords.map((point) => ({
    x: point.x - slot * 0.3 + slot * 0.15,
    y: point.y,
    w: Math.max(2, slot * 0.6),
    h: Math.max(1, props.height - pad - point.y),
  }))

  return { path, area, bars, baselineY: props.baseline === null ? null : y(props.baseline) }
})
</script>

<template>
  <svg
    class="spark"
    :width="props.width"
    :height="props.height"
    :viewBox="`0 0 ${props.width} ${props.height}`"
    :style="{ color }"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="geometry.path">
      <template v-if="props.kind === 'bar'">
        <rect v-for="(bar, index) in geometry.bars" :key="index" :x="bar.x" :y="bar.y" :width="bar.w" :height="bar.h" fill="currentcolor" opacity="0.75" rx="1" />
      </template>
      <template v-else>
        <path v-if="props.kind === 'area'" :d="geometry.area" fill="currentcolor" opacity="0.16" />
        <path :d="geometry.path" fill="none" stroke="currentcolor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" />
      </template>
      <line
        v-if="geometry.baselineY !== null"
        x1="0"
        :y1="geometry.baselineY"
        :x2="props.width"
        :y2="geometry.baselineY"
        stroke="currentcolor"
        stroke-width="1"
        stroke-dasharray="3 3"
        opacity="0.5"
      />
    </template>
  </svg>
</template>

<style scoped>
.spark {
  display: block;
  overflow: visible;
}
</style>
