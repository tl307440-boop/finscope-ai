<script setup lang="ts">
/**
 * 卡内小指标格（效果图里 8 个一横排的那种）。
 * 标签 + 期间 + 大数值 + 环比 + 迷你图。
 */
withDefaults(
  defineProps<{
    label: string
    value: string | number | null
    /** 数值上方的期间说明，例如「2024-10」「2024 Q3（季调，年化）」 */
    period?: string
    unit?: string
    info?: string
    /** 环比文案，例如「较上期」 */
    deltaLabel?: string
  }>(),
  { period: "", unit: "", info: "", deltaLabel: "较上期" },
)
</script>

<template>
  <div class="tile">
    <header>
      <span class="label">{{ label }}</span>
      <span v-if="info" class="info" :title="info">?</span>
    </header>
    <p v-if="period" class="period">{{ period }}</p>
    <p class="value">
      <template v-if="value === null || value === undefined || value === ''">—</template>
      <template v-else>{{ value }}<small v-if="unit">{{ unit }}</small></template>
    </p>
    <footer>
      <span class="delta-label">{{ deltaLabel }}</span>
      <slot name="delta" />
      <span v-if="$slots.chart" class="chart"><slot name="chart" /></span>
    </footer>
  </div>
</template>

<style scoped>
.tile {
  background: linear-gradient(180deg, rgb(96 140 220 / 7%), rgb(96 140 220 / 2%));
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  padding: 11px 13px;
  min-width: 0;
}

header {
  display: flex;
  align-items: center;
  gap: 6px;
}

.label {
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.info {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 9px;
  cursor: help;
}

.period {
  margin: 3px 0 0;
  font-size: 11px;
  color: var(--muted);
}

.value {
  margin: 6px 0 6px;
  font-family: var(--font-num);
  font-size: 24px;
  font-weight: 600;
  line-height: 1.1;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.value small {
  margin-left: 2px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
}

footer {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.delta-label {
  white-space: nowrap;
}

.chart {
  margin-left: auto;
}
</style>
