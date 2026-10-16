<script setup lang="ts">
import { computed } from "vue"

/**
 * 状态标签。效果图里「得到支持 / 尚待观察 / 受到挑战 / 证据不足」「成功 / 失败 / 运行中」
 * 「高 / 中 / 低」都是它，只换语气不换写法。
 *
 * 文案同时接受 text 与 label 两个名字（label 是 text 的别名），
 * 语气里 info 等价于 accent，避免调用方在两套词汇之间来回改。
 */
const props = withDefaults(
  defineProps<{
    text?: string
    /** text 的别名，两个都传时以 text 为准 */
    label?: string
    /** ok 绿 / warn 琥珀 / danger 红 / muted 灰 / accent(或 info) 蓝 */
    tone?: "ok" | "warn" | "danger" | "muted" | "accent" | "info"
    /** 实心小圆点，用于任务状态 */
    dot?: boolean
  }>(),
  { text: "", label: "", tone: "muted", dot: false },
)

const caption = computed(() => props.text || props.label)
const toneClass = computed(() => (props.tone === "info" ? "accent" : props.tone))
</script>

<template>
  <span class="pill" :class="`tone-${toneClass}`">
    <i v-if="dot" class="dot" aria-hidden="true" />
    {{ caption }}
  </span>
</template>

<style scoped>
.pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
  box-shadow: 0 0 0 1px color-mix(in srgb, currentcolor 22%, transparent) inset;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
}

.tone-ok {
  background: rgb(52 211 153 / 14%);
  color: var(--ok);
}

.tone-warn {
  background: rgb(251 191 36 / 14%);
  color: var(--warn);
}

.tone-danger {
  background: rgb(248 113 113 / 14%);
  color: var(--danger);
}

.tone-muted {
  background: rgb(149 166 190 / 12%);
  color: var(--muted);
}

.tone-accent {
  background: var(--accent-soft);
  color: var(--accent);
}
</style>
