<script setup lang="ts">
/**
 * 卡片外壳。效果图里每张卡片的统一头部：强调色图标 + 标题 + ⓘ + 右上角「查看详情 ›」。
 * 页面只负责往插槽里放内容。
 */
withDefaults(
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
  }>(),
  { title: "", info: "", moreText: "", moreTo: "", compact: false, flush: false },
)
</script>

<template>
  <section class="card" :class="{ compact, flush }">
    <header v-if="title || $slots.actions || moreText" class="head">
      <div class="title">
        <span v-if="$slots.icon" class="icon" aria-hidden="true"><slot name="icon" /></span>
        <h2 v-if="title">{{ title }}</h2>
        <span v-if="info" class="info" :title="info">?</span>
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
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 18px 20px;
  min-width: 0;
}

.card.compact {
  padding: 14px 16px;
}

.card.flush {
  padding: 18px 0 0;
}

.card.flush > :deep(*) {
  padding-left: 20px;
  padding-right: 20px;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.card.flush .head {
  margin-bottom: 12px;
}

.title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.icon {
  display: inline-flex;
  color: var(--accent);
  font-size: 16px;
}

h2 {
  margin: 0;
  font-size: 15px;
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

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
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
