<script setup lang="ts">
/** 0–100 规则分的渐变刻度条（经济热度、流动性环境）。 */
const props = defineProps<{
  title: string
  score: number | null
  label: string
  ends: readonly [string, string, string]
  gradient: string
  info?: string
}>()
</script>

<template>
  <div class="gauge" :title="props.info">
    <div class="lab">
      <b>{{ props.title }}</b>
      <span class="badge">{{ props.label }}</span>
      <span class="num score">{{ props.score === null ? "—" : props.score.toFixed(1) }}</span>
    </div>
    <div class="track" :style="{ background: props.gradient }">
      <i v-if="props.score !== null" :style="{ left: `${props.score}%` }" />
    </div>
    <div class="ends">
      <span v-for="item in props.ends" :key="item">{{ item }}</span>
    </div>
  </div>
</template>

<style scoped>
.gauge {
  margin-top: 12px;
}

.lab {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.badge {
  padding: 0 6px;
  border-radius: 4px;
  font-size: 11.5px;
  line-height: 18px;
  background: var(--accent-soft);
  color: var(--accent);
}

.score {
  margin-left: auto;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-strong);
}

.track {
  position: relative;
  height: 8px;
  border-radius: 5px;
}

.track i {
  position: absolute;
  top: -4px;
  width: 14px;
  height: 14px;
  border: 3px solid var(--panel);
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 0 0 1px #fff, 0 0 10px rgb(255 255 255 / 50%);
  transform: translateX(-50%);
}

.ends {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--faint);
}
</style>
