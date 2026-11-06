<script setup lang="ts">
/**
 * 面板内的分区：左侧色条 + 小标题 + 一条延伸的分隔线，右侧可放操作。
 * 用来把一个面板里的不同区域（例如「涨跌广度」「涨停与接力」）分开，避免一整块糊在一起。
 */
defineProps<{ title: string; note?: string }>()
</script>

<template>
  <section class="panel-section">
    <header class="ps-head">
      <span class="ps-title">{{ title }}</span>
      <span v-if="note" class="ps-note" :title="note">{{ note }}</span>
      <span class="ps-rule" aria-hidden="true" />
      <span v-if="$slots.actions" class="ps-actions"><slot name="actions" /></span>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.panel-section + .panel-section {
  margin-top: 16px;
}

.ps-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  min-width: 0;
}

.ps-title {
  position: relative;
  padding-left: 9px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
}

.ps-title::before {
  content: "";
  position: absolute;
  left: 0;
  top: 2px;
  bottom: 2px;
  width: 3px;
  border-radius: 2px;
  background: var(--accent);
}

.ps-note {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--faint);
}

.ps-rule {
  flex: 1;
  min-width: 12px;
  height: 1px;
  background: linear-gradient(90deg, var(--line), transparent);
}

.ps-actions {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
</style>
