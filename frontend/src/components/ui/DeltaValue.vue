<script setup lang="ts">
/**
 * 涨跌数值。效果图里所有价格、涨跌幅、环比列都用它渲染。
 * 硬规矩：涨用 A股红、跌用 A股绿，并且一律带正负号，色盲也能读。
 */
import { computed } from "vue"

const props = withDefaults(
  defineProps<{
    value: number | string | null | undefined
    /** 单位后缀，例如 "%"、"pct"、"亿元" */
    suffix?: string
    /** false 时不染色（绝对读数，例如 CPI 同比 2.6%） */
    colored?: boolean
    /** 这个字段本身就是涨跌时置 true：即便不染色也一定带上正负号。 */
    signed?: boolean
    /** 小数位。涨跌幅、比率一律两位；缺省两位，不要在这里暴露浮点尾巴。 */
    digits?: number
    size?: "sm" | "md" | "lg"
  }>(),
  { suffix: "", colored: true, signed: false, digits: 2, size: "md" },
)

const numeric = computed<number | null>(() => {
  if (typeof props.value === "number") {
    return Number.isFinite(props.value) ? props.value : null
  }
  return null
})

const text = computed<string>(() => {
  if (props.value === null || props.value === undefined || props.value === "") {
    return "—"
  }
  if (numeric.value === null) {
    return String(props.value)
  }
  const shown = Math.abs(numeric.value)
  // 只有真的在表达「变化」时才补 "+"：涨价读 +3,707.48 是错的，价格就是 3,707.48。
  // 负号永远保留，规格书 §1.2 要求色盲也能靠正负号读懂方向。
  const wantsSign = props.colored || props.signed
  const sign = numeric.value > 0 ? (wantsSign ? "+" : "") : numeric.value < 0 ? "-" : ""
  const body = shown.toLocaleString("zh-CN", {
    minimumFractionDigits: props.digits > 0 ? props.digits : 0,
    maximumFractionDigits: props.digits,
  })
  return `${sign}${body}${props.suffix}`
})

const direction = computed<"up" | "down" | "flat">(() => {
  if (numeric.value === null) {
    return "flat"
  }
  if (numeric.value > 0) {
    return "up"
  }
  return numeric.value < 0 ? "down" : "flat"
})

/** 文本分支下没有数值可比，就从字符串里认符号。 */
const tone = computed<string>(() => {
  if (!props.colored) {
    return "flat"
  }
  if (direction.value !== "flat") {
    return direction.value
  }
  const raw = String(props.value ?? "")
  if (raw.startsWith("+") || raw.startsWith("-")) {
    return raw.startsWith("-") ? "down" : "up"
  }
  return "flat"
})
</script>

<template>
  <span class="delta" :class="[`is-${tone}`, `size-${props.size}`]">{{ text }}</span>
</template>

<style scoped>
.delta {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.size-sm {
  font-size: 12px;
}

.size-md {
  font-size: 13px;
}

.size-lg {
  font-size: 15px;
  font-weight: 600;
}

.is-up {
  color: var(--rise);
}

.is-down {
  color: var(--fall);
}

.is-flat {
  color: var(--muted);
}
</style>
