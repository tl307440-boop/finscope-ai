<script setup lang="ts">
/**
 * 顶部大指标卡。
 * 布局：色块图标 + 标题 + 右上角「查看详情 ›」/ 大数值 + 副文案 + 右侧迷你图。
 */
import { computed } from "vue"
import { toneForTitle, type PanelTone } from "./tone"

const props = withDefaults(
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
    tone?: PanelTone | ""
  }>(),
  { value: null, unit: "", caption: "", info: "", moreText: "查看详情", moreTo: "", tone: "" },
)

const resolvedTone = computed<PanelTone>(() => props.tone || toneForTitle(props.title))
const empty = computed(() => props.value === null || props.value === undefined || props.value === "")
</script>

<template>
  <section class="stat" :class="`tone-${resolvedTone}`">
    <header class="head">
      <span v-if="$slots.icon" class="icon" aria-hidden="true"><slot name="icon" /></span>
      <h3>{{ title }}</h3>
      <span v-if="info" class="info" :title="info">i</span>
      <router-link v-if="moreTo" class="more" :to="moreTo">{{ moreText }} ›</router-link>
    </header>
    <div class="body">
      <div class="left">
        <p class="value" :class="{ empty }">
          <template v-if="empty">—</template>
          <template v-else>{{ value }}<small v-if="unit">{{ unit }}</small></template>
        </p>
        <p v-if="caption" class="caption" :title="caption">{{ caption }}</p>
        <slot name="below" />
      </div>
      <div v-if="$slots.chart" class="chart"><slot name="chart" /></div>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.stat {
  --tone: var(--accent);
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(240px 120px at 100% 0%, color-mix(in srgb, var(--tone) 10%, transparent), transparent 70%),
    linear-gradient(180deg, var(--panel-top), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 13px 16px 14px;
  min-width: 0;
}

.stat::before {
  content: "";
  position: absolute;
  inset: 0 16px auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--tone) 75%, transparent), transparent);
}

.tone-accent {
  --tone: var(--accent);
}

.tone-cyan {
  --tone: var(--cyan);
}

.tone-violet {
  --tone: var(--violet);
}

.tone-orange {
  --tone: var(--orange);
}

.tone-ok {
  --tone: var(--ok);
}

.tone-warn {
  --tone: var(--warn);
}

.tone-danger {
  --tone: var(--danger);
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  flex: none;
  border-radius: 6px;
  color: var(--tone);
  background: color-mix(in srgb, var(--tone) 16%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--tone) 28%, transparent) inset;
  font-size: 14px;
}

h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.info {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line);
  color: var(--faint);
  font-size: 9px;
  font-style: italic;
  font-family: Georgia, serif;
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
  font-family: var(--font-num);
  font-size: 30px;
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: 0.01em;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.value.empty {
  color: var(--faint);
}

.value small {
  margin-left: 3px;
  font-family: var(--font-ui);
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.caption {
  margin: 5px 0 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.chart {
  flex: none;
}
</style>
