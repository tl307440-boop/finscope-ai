<script setup lang="ts">
/**
 * 顶部大指标卡（效果图第一排那四张）。
 * 布局：图标 + 标题 + 右上角「查看详情 ›」/ 大数值 + 副文案 + 右侧迷你图。
 */
withDefaults(
  defineProps<{
    title: string
    /** 大数值；没有数据时传 null，组件显示「—」并保留副文案里的原因 */
    value?: string | number | null
    /** 值后面的小单位，例如「/100」「个」「只」 */
    unit?: string
    /** 数值下方的说明文字 */
    caption?: string
    info?: string
    moreText?: string
    moreTo?: string
  }>(),
  { value: null, unit: "", caption: "", info: "", moreText: "查看详情", moreTo: "" },
)
</script>

<template>
  <section class="stat">
    <header class="head">
      <span v-if="$slots.icon" class="icon" aria-hidden="true"><slot name="icon" /></span>
      <h3>{{ title }}</h3>
      <span v-if="info" class="info" :title="info">?</span>
      <router-link v-if="moreTo" class="more" :to="moreTo">{{ moreText }} ›</router-link>
      <span v-else-if="moreText" class="more">{{ moreText }} ›</span>
    </header>
    <div class="body">
      <div class="left">
        <p class="value">
          <template v-if="value === null || value === undefined || value === ''">—</template>
          <template v-else>{{ value }}<small v-if="unit">{{ unit }}</small></template>
        </p>
        <p v-if="caption" class="caption">{{ caption }}</p>
        <slot name="below" />
      </div>
      <div v-if="$slots.chart" class="chart"><slot name="chart" /></div>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.stat {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px 16px;
  min-width: 0;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.icon {
  display: inline-flex;
  color: var(--accent);
  font-size: 16px;
}

h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
}

.info {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 10px;
  cursor: help;
}

.more {
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
  text-decoration: none;
  white-space: nowrap;
}

a.more:hover {
  color: var(--accent);
}

.body {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}

.left {
  min-width: 0;
}

.value {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.value small {
  margin-left: 2px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.caption {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.chart {
  flex: none;
}
</style>
