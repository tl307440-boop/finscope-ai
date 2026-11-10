<script setup lang="ts">
/**
 * 看板数据状态条：盘中/收盘、交易日、更新时间、来源，以及上游缺失项的原因。
 * 缺失项必须说明原因（AGENTS.md：不做模拟兜底，未接通的数据要如实标注）；
 * 缺项与口径说明默认折叠成小标签，点开才展开，避免一长串文字糊在面板底部。
 */
import { computed, ref } from "vue"

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
const sourceText = computed(() => props.meta?.sources.join(" / ") ?? "")
const open = ref<"" | "missing" | "notes">("")

function toggle(which: "missing" | "notes"): void {
  open.value = open.value === which ? "" : which
}
</script>

<template>
  <div v-if="meta" class="panorama-status" role="status">
    <div class="ps-line">
      <span class="ps-badge" :class="`is-${meta.session}`">{{ sessionText }}</span>
      <span v-if="meta.tradeDate" class="ps-item">交易日 {{ meta.tradeDate }}</span>
      <span v-if="updatedText" class="ps-item">更新 {{ updatedText }}</span>
      <span v-if="meta.stale" class="ps-badge is-stale">上游暂不可用，展示的是缓存</span>
      <span v-if="refreshError" class="ps-badge is-stale">刷新失败：{{ refreshError }}</span>
      <span v-if="sourceText" class="ps-item ps-sources" :title="sourceText">来源：{{ sourceText }}</span>
      <span class="ps-spacer" />
      <button
        v-if="unavailableList.length"
        type="button"
        class="ps-toggle is-warn"
        :class="{ on: open === 'missing' }"
        @click="toggle('missing')"
      >
        缺项 {{ unavailableList.length }}
        <span aria-hidden="true">{{ open === "missing" ? "▴" : "▾" }}</span>
      </button>
      <button
        v-if="meta.notes.length"
        type="button"
        class="ps-toggle"
        :class="{ on: open === 'notes' }"
        @click="toggle('notes')"
      >
        口径说明
        <span aria-hidden="true">{{ open === "notes" ? "▴" : "▾" }}</span>
      </button>
    </div>
    <ul v-if="open === 'missing'" class="ps-detail ps-unavailable">
      <li v-for="[key, reason] in unavailableList" :key="key">
        <b>{{ key }}</b>：{{ reason }}
      </li>
    </ul>
    <ul v-if="open === 'notes'" class="ps-detail ps-notes">
      <li v-for="note in meta.notes" :key="note">{{ note }}</li>
    </ul>
  </div>
</template>

<style scoped>
.panorama-status {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--faint);
}

.ps-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px 12px;
  min-width: 0;
}

.ps-spacer {
  flex: 1;
}

.ps-item {
  white-space: nowrap;
}

.ps-sources {
  min-width: 0;
  max-width: 46%;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ps-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-weight: 600;
  background: var(--panel-soft);
  border: 1px solid var(--line);
  color: var(--muted);
}

.ps-badge.is-intraday {
  color: var(--rise);
  border-color: color-mix(in srgb, var(--rise) 40%, transparent);
  background: color-mix(in srgb, var(--rise) 8%, transparent);
}

.ps-badge.is-stale {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 45%, transparent);
  background: color-mix(in srgb, var(--warn) 9%, transparent);
}

.ps-toggle {
  padding: 1px 9px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.ps-toggle:hover,
.ps-toggle.on {
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 40%, var(--line));
  background: var(--accent-soft);
}

.ps-toggle.is-warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, var(--line));
}

.ps-toggle.is-warn:hover,
.ps-toggle.is-warn.on {
  background: color-mix(in srgb, var(--warn) 10%, transparent);
  color: var(--warn);
}

.ps-detail {
  margin: 0;
  padding: 8px 12px 8px 26px;
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  border: 1px solid var(--line-soft);
  line-height: 1.7;
  max-height: 180px;
  overflow: auto;
}

.ps-unavailable {
  color: var(--warn);
}

.ps-notes {
  color: var(--muted);
}
</style>
