<script setup lang="ts">
/**
 * 页面统一状态块。规格书 §1.2 要求五种全局状态：
 * loading（骨架）/ empty（无数据，说明原因）/ error（可重试）/ stale（带最近有效日期）/ denied（权限）。
 *
 * 关键约定：empty 不等于 0。上游没接通时显示「暂无数据 + 原因」，不要编造数字。
 */
withDefaults(
  defineProps<{
    state: "loading" | "empty" | "error" | "stale" | "denied" | "ready"
    /** 可选的标题；留空时只显示一行说明 */
    title?: string
    /** empty/stale/denied 的原因说明；error 时放错误信息 */
    message?: string
    /** 最近一次有效数据日期（stale 用） */
    asOf?: string
    /** 骨架行数 */
    rows?: number
  }>(),
  { title: "", message: "", asOf: "", rows: 3 },
)

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="state === 'loading'" class="skeleton-block" aria-busy="true" aria-live="polite">
    <p v-if="message" class="loading-caption">{{ message }}</p>
    <span v-for="row in rows" :key="row" class="skeleton" />
  </div>

  <div v-else-if="state === 'error'" class="state error" role="alert">
    <div class="state-text">
      <strong v-if="title">{{ title }}</strong>
      <p>{{ message || "加载失败" }}</p>
    </div>
    <button class="retry" type="button" @click="emit('retry')">重试</button>
  </div>

  <div v-else-if="state === 'denied'" class="state denied" role="status">
    <div class="state-text">
      <strong v-if="title">{{ title }}</strong>
      <p>{{ message || "当前账号无权访问该数据，请联系管理员开通" }}</p>
    </div>
    <slot />
  </div>

  <div v-else-if="state === 'stale'" class="state stale" role="status">
    <div class="state-text">
      <strong v-if="title">{{ title }}</strong>
      <p>
        {{ message || "数据源暂时不可用" }}
        <span v-if="asOf">（最近有效数据：{{ asOf }}）</span>
      </p>
    </div>
    <button class="retry" type="button" @click="emit('retry')">重试</button>
  </div>

  <div v-else-if="state === 'empty'" class="state empty" role="status">
    <div class="state-text">
      <strong v-if="title">{{ title }}</strong>
      <p>{{ message || "暂无数据" }}</p>
    </div>
    <slot />
  </div>

  <slot v-else />
</template>

<style scoped>
.skeleton-block {
  display: grid;
  gap: 10px;
}

.loading-caption {
  margin: 0 0 2px;
  color: var(--muted);
  font-size: 13px;
}

.skeleton {
  display: block;
  height: 16px;
  min-height: 0;
  padding: 0;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--panel-soft) 25%, var(--panel-raise) 37%, var(--panel-soft) 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton {
    animation: none;
  }
}

.state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 24px 18px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px dashed var(--line);
  color: var(--muted);
  font-size: 13px;
}

.state p {
  margin: 0;
}

.state-text {
  display: grid;
  gap: 4px;
}

.state-text strong {
  color: var(--text);
  font-size: 15px;
  font-weight: 600;
}

.state.error {
  color: var(--danger);
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
}

.state.stale {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
}

.retry {
  flex: none;
  appearance: none;
  background: none;
  border: 1px solid currentColor;
  border-radius: 6px;
  padding: 4px 12px;
  color: inherit;
  font-size: 12px;
  cursor: pointer;
}

.retry:hover {
  background: color-mix(in srgb, currentColor 12%, transparent);
}
</style>
