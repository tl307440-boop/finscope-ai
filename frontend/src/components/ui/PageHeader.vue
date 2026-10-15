<script setup lang="ts">
/**
 * 页面标题区。效果图里每页顶部那条：大标题 + 同一行的副标题 + 右侧操作按钮 + 可选页签行。
 */
withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    /** 右上角是否要一个「自定义看板」这类默认按钮，交给 actions 插槽更灵活 */
    dense?: boolean
  }>(),
  { subtitle: "", dense: false },
)
</script>

<template>
  <header class="page-header" :class="{ dense }">
    <div class="row">
      <div class="titles">
        <h1>{{ title }}</h1>
        <p v-if="subtitle" class="subtitle">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.actions" class="actions"><slot name="actions" /></div>
    </div>
    <div v-if="$slots.tabs || $slots.filters" class="tabs-row">
      <slot name="tabs" />
      <div v-if="$slots.filters" class="filters"><slot name="filters" /></div>
    </div>
  </header>
</template>

<style scoped>
.page-header {
  margin-bottom: 16px;
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.titles {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}

h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  white-space: nowrap;
}

.subtitle {
  margin: 0;
  font-size: 14px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

.tabs-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 14px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 10px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
}

.dense {
  margin-bottom: 12px;
}
</style>
