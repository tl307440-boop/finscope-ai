<script setup lang="ts">
/**
 * 走势图的「图例选择器」：每个指标一枚可开关的色块标签，数量不限；
 * 右侧可切换时间窗口。选择结果交给页面去聚合到同一张图里。
 */
interface PickerOption {
  code: string
  label: string
  color: string
  /** 没有可画的数据时置灰，不允许选中 */
  disabled?: boolean
  /** 悬浮说明，例如单位与频率 */
  hint?: string
}

const props = defineProps<{
  options: readonly PickerOption[]
  modelValue: readonly string[]
  years: number
  yearOptions: readonly number[]
}>()

const emit = defineEmits<{
  "update:modelValue": [string[]]
  "update:years": [number]
}>()

function isOn(code: string): boolean {
  return props.modelValue.includes(code)
}

function toggle(option: PickerOption): void {
  if (option.disabled) {
    return
  }
  // 保持选项原有顺序，图例与配色不会因点击先后跳动。
  const next = props.options
    .filter((item) => (item.code === option.code ? !isOn(item.code) : isOn(item.code)))
    .map((item) => item.code)
  emit("update:modelValue", next)
}

function selectAll(): void {
  emit(
    "update:modelValue",
    props.options.filter((item) => !item.disabled).map((item) => item.code),
  )
}

function clearAll(): void {
  emit("update:modelValue", [])
}
</script>

<template>
  <div class="picker">
    <div class="chips" role="group" aria-label="选择要聚合的指标">
      <button
        v-for="option in options"
        :key="option.code"
        type="button"
        class="chip"
        :class="{ on: isOn(option.code), off: !isOn(option.code), disabled: option.disabled }"
        :style="{ '--chip': option.color }"
        :aria-pressed="isOn(option.code)"
        :disabled="option.disabled"
        :title="option.disabled ? `${option.label}：暂无可画的数据` : option.hint || option.label"
        @click="toggle(option)"
      >
        <i aria-hidden="true" />{{ option.label }}
      </button>
    </div>
    <div class="tools">
      <button type="button" class="link" @click="selectAll">全选</button>
      <button type="button" class="link" @click="clearAll">清空</button>
      <span v-if="yearOptions.length" class="sep" aria-hidden="true" />
      <button
        v-for="year in yearOptions"
        :key="year"
        type="button"
        class="link range"
        :class="{ active: year === years }"
        :aria-pressed="year === years"
        @click="emit('update:years', year)"
      >
        {{ year > 0 ? `${year}年` : "全部" }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.picker {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 6px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.chip {
  appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 9px;
  border: 1px solid color-mix(in srgb, var(--chip) 55%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--chip) 14%, transparent);
  color: var(--text);
  font-size: 12px;
  line-height: 20px;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 120ms ease,
    opacity 120ms ease;
}

.chip i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--chip);
}

.chip.off {
  border-color: var(--line);
  background: transparent;
  color: var(--muted);
}

.chip.off i {
  background: transparent;
  box-shadow: 0 0 0 1px var(--muted) inset;
}

.chip:hover:not(.disabled) {
  background: color-mix(in srgb, var(--chip) 24%, transparent);
}

.chip.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.tools {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.link {
  appearance: none;
  background: none;
  border: 0;
  padding: 0;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.link:hover {
  color: var(--accent);
}

.link.range.active {
  color: var(--accent);
  font-weight: 600;
}

.sep {
  width: 1px;
  height: 10px;
  background: var(--line);
}
</style>
