<script setup lang="ts">
/**
 * 助手这一轮查了哪些数据：每一步一行，状态用颜色和文字一起表示。
 * 还在查时展开，查完收成一行摘要，点开可以看。
 */
import { computed } from "vue"
import { stepSummary, type AssistantStep, type StepStatus } from "../../api/assistantBlocks"

const props = defineProps<{ steps: AssistantStep[] }>()

const STATUS_TEXT: Record<StepStatus, string> = {
  running: "进行中",
  ok: "已取到",
  stale: "数据偏旧",
  unavailable: "没有数据",
}

const summary = computed(() => stepSummary(props.steps))
const running = computed(() => props.steps.some((step) => step.status === "running"))
</script>

<template>
  <details v-if="steps.length" class="steps" :open="running">
    <summary>{{ summary }}</summary>
    <ol>
      <li v-for="step in steps" :key="step.id" :data-status="step.status">
        <span class="dot" aria-hidden="true" />
        <span class="label">{{ step.label }}</span>
        <span class="state">{{ STATUS_TEXT[step.status] }}</span>
      </li>
    </ol>
  </details>
</template>

<style scoped>
.steps {
  margin: 0 0 8px;
  font-size: 12.5px;
  color: var(--muted);
}

summary {
  cursor: pointer;
  user-select: none;
}

ol {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
  display: grid;
  gap: 4px;
}

li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}

.dot {
  flex: 0 0 7px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--muted);
  align-self: center;
}

li[data-status="ok"] .dot {
  background: var(--ok);
}

li[data-status="stale"] .dot,
li[data-status="unavailable"] .dot {
  background: var(--warn);
}

li[data-status="running"] .dot {
  background: var(--accent);
}

.label {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.state {
  flex: 0 0 auto;
}
</style>
