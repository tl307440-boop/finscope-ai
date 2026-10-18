<script setup lang="ts">
/** 一格读数：标题 + 大号数值 + 可选说明与色调。只排版，不做任何计算。 */
withDefaults(
  defineProps<{
    label: string
    value: string
    hint?: string
    tone?: "" | "cool" | "hot" | "warn"
  }>(),
  { hint: "", tone: "" },
)
</script>

<template>
  <div class="stat" :class="tone ? `tone-${tone}` : ''">
    <span class="stat-label">{{ label }}</span>
    <strong class="stat-value tabular">{{ value }}</strong>
    <span v-if="hint" class="stat-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.stat {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 10px 14px;
  border: 1px solid var(--line-soft);
  background: rgb(96 140 220 / 5%);
}

.stat-label {
  font-size: 12px;
  color: var(--muted);
}

.stat-value {
  font-family: var(--font-num);
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--text-strong);
}

.stat-hint {
  font-size: 12px;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.tone-cool .stat-value {
  color: #22d3ee;
}

.tone-hot .stat-value {
  color: #fb923c;
}

.tone-warn .stat-value {
  color: var(--warn);
}
</style>
