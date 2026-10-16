<script setup lang="ts">
/**
 * 下划线式页签（效果图里的「公司概况 / 财务总览 / 财务趋势 / …」）。
 * 给 to 就走路由，不给就用 v-model 在页内切。
 */
const props = withDefaults(
  defineProps<{
    tabs: readonly { key: string; label: string; to?: string }[]
    modelValue?: string
  }>(),
  { modelValue: "" },
)

const emit = defineEmits<{ "update:modelValue": [string] }>()

function pick(tab: { key: string; to?: string }): void {
  if (tab.to) {
    return
  }
  emit("update:modelValue", tab.key)
}

function isActive(tab: { key: string; to?: string }): boolean {
  if (tab.to) {
    return false
  }
  return props.modelValue === tab.key
}
</script>

<template>
  <nav class="section-tabs" role="tablist">
    <template v-for="tab in tabs" :key="tab.key">
      <router-link v-if="tab.to" class="tab" :to="tab.to" role="tab">{{ tab.label }}</router-link>
      <button
        v-else
        class="tab"
        type="button"
        role="tab"
        :class="{ active: isActive(tab) }"
        :aria-selected="isActive(tab)"
        @click="pick(tab)"
      >
        {{ tab.label }}
      </button>
    </template>
    <slot />
  </nav>
</template>

<style scoped>
.section-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.tab {
  appearance: none;
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  padding: 6px 14px;
  margin-bottom: -11px;
  color: var(--muted);
  font-size: 14px;
  text-decoration: none;
  cursor: pointer;
  white-space: nowrap;
}

.tab:hover {
  color: var(--text);
}

.tab.active,
.tab.router-link-active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
</style>
