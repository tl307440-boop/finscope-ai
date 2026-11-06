<script setup lang="ts">
/**
 * 看板数据状态条：盘中/收盘、交易日、更新时间、来源，以及上游缺失项的原因。
 * 缺失项必须说明原因（AGENTS.md：不做模拟兜底，未接通的数据要如实标注）。
 */
import { computed } from "vue"

import type { PanoramaMeta } from "../../api/aSharePanorama"

const props = defineProps<{
  meta: PanoramaMeta | null
  /** 刷新失败但仍展示旧数据时的提示 */
  refreshError?: string
}>()

const sessionText = computed(() => {
  if (!props.meta) return ""
  if (props.meta.session === "intraday") return "盘中实时"
  if (props.meta.session === "close") return "已收盘"
  return "交易时段未知"
})

const updatedText = computed(() => {
  const raw = props.meta?.fetchedAt
  if (!raw) return ""
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleTimeString("zh-CN", { hour12: false })
})

const unavailableList = computed(() => Object.entries(props.meta?.unavailable ?? {}))
</script>

<template>
  <div v-if="meta" class="panorama-status" role="status">
    <div class="ps-line">
      <span class="ps-badge" :class="`is-${meta.session}`">{{ sessionText }}</span>
      <span v-if="meta.tradeDate" class="ps-item">交易日 {{ meta.tradeDate }}</span>
      <span v-if="updatedText" class="ps-item">更新 {{ updatedText }}</span>
      <span v-if="meta.stale" class="ps-badge is-stale">上游暂不可用，展示的是缓存</span>
      <span v-if="refreshError" class="ps-badge is-stale">刷新失败：{{ refreshError }}</span>
      <span v-if="meta.sources.length" class="ps-item ps-sources">
        来源：{{ meta.sources.join(" / ") }}
      </span>
    </div>
    <ul v-if="unavailableList.length" class="ps-unavailable">
      <li v-for="[key, reason] in unavailableList" :key="key">
        <b>{{ key }}</b>：{{ reason }}
      </li>
    </ul>
    <ul v-if="meta.notes.length" class="ps-notes">
      <li v-for="note in meta.notes" :key="note">{{ note }}</li>
    </ul>
  </div>
</template>

<style scoped>
.panorama-status {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.ps-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.ps-badge {
  padding: 1px 7px;
  border-radius: 999px;
  font-weight: 600;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.ps-badge.is-intraday {
  color: var(--rise);
  border-color: rgba(246, 70, 93, 0.35);
}

.ps-badge.is-stale {
  color: #b45309;
  border-color: rgba(245, 158, 11, 0.45);
  background: rgba(245, 158, 11, 0.08);
}

.ps-sources {
  opacity: 0.85;
}

.ps-unavailable,
.ps-notes {
  margin: 0;
  padding-left: 16px;
  line-height: 1.55;
}

.ps-unavailable {
  color: #b45309;
}
</style>
