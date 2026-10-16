<script setup lang="ts">
/** 引用锚点：公告与 SEC 给可点击的原文链接，日线 / FRED 只显示锚点，不伪造链接。 */
import type { CitationRef } from "../../../api/simulation"

defineProps<{ items: CitationRef[] }>()

function href(item: CitationRef): string | null {
  return item.ref.startsWith("https://") ? item.ref : null
}
</script>

<template>
  <span v-if="items.length" class="chips">
    <template v-for="(item, index) in items.slice(0, 2)" :key="index">
      <a
        v-if="href(item)"
        class="chip"
        :href="href(item) ?? undefined"
        target="_blank"
        rel="noopener noreferrer"
        :title="item.label"
      >{{ item.source }} · {{ item.observedAt }}</a>
      <span v-else class="chip" :title="item.label">{{ item.source }} · {{ item.observedAt }}</span>
    </template>
  </span>
</template>

<style scoped>
.chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-left: 6px;
  vertical-align: middle;
}

.chip {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  border: 1px solid var(--line);
  font-size: 11px;
  line-height: 18px;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}

a.chip:hover,
a.chip:focus-visible {
  color: var(--cyan);
  border-color: var(--cyan);
}
</style>
