<script setup lang="ts">
/**
 * 卡片外壳。统一头部：色块图标 + 标题 + ⓘ + 右上角「查看详情 ›」。
 * 页面只负责往插槽里放内容。
 */
import { computed } from "vue"
import { toneForTitle, type PanelTone } from "./tone"

const props = withDefaults(
  defineProps<{
    title?: string
    /** 标题右边的 ⓘ 悬浮说明 */
    info?: string
    /** 右上角链接文案，例如「查看详情」「查看全部」 */
    moreText?: string
    /** 右上角链接目标，给路由则渲染 router-link */
    moreTo?: string
    /** 紧凑内边距（卡片里再套卡片时用） */
    compact?: boolean
    /** 去掉内边距，交给表格自己撑满 */
    flush?: boolean
    /** 图标色块的色调；不给时按标题语义选 */
    tone?: PanelTone | ""
  }>(),
  { title: "", info: "", moreText: "", moreTo: "", compact: false, flush: false, tone: "" },
)

const resolvedTone = computed<PanelTone>(() => props.tone || toneForTitle(props.title))
</script>

<template>
  <section class="card" :class="[{ compact, flush }, `tone-${resolvedTone}`]">
    <header v-if="title || $slots.actions || moreText" class="head">
      <div class="title">
        <span v-if="$slots.icon" class="icon" aria-hidden="true"><slot name="icon" /></span>
        <h2 v-if="title">{{ title }}</h2>
        <span v-if="info" class="info" :title="info">i</span>
      </div>
      <div class="actions">
        <slot name="actions" />
        <router-link v-if="moreText && moreTo" class="more" :to="moreTo">{{ moreText }} ›</router-link>
        <span v-else-if="moreText" class="more">{{ moreText }} ›</span>
      </div>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.card {
  --tone: var(--accent);
  position: relative;
  background: linear-gradient(180deg, var(--panel-top), var(--panel) 140px);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px 16px;
  min-width: 0;
  transition: border-color 160ms ease;
}

/* 顶边一道冷光，从左侧色调渐隐 */
.card::before {
  content: "";
  position: absolute;
  inset: -1px 16px auto;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    color-mix(in srgb, var(--tone) 70%, transparent) 20%,
    var(--edge-light) 55%,
    transparent
  );
  pointer-events: none;
}

.card:hover {
  border-color: color-mix(in srgb, var(--tone) 28%, var(--line));
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

.card.compact {
  padding: 12px 14px;
}

.card.flush {
  padding: 14px 0 6px;
}

.card.flush > :deep(*) {
  padding-left: 16px;
  padding-right: 16px;
}

.card.flush > :deep(.el-table) {
  padding: 0;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  min-height: 26px;
}

.card.flush .head {
  margin-bottom: 10px;
}

.title {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
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

h2 {
  margin: 0;
  font-size: 15px;
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

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  flex: none;
}

.more {
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}

.more:hover {
  color: var(--accent);
}
</style>
