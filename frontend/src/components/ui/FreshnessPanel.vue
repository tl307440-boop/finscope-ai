<script setup lang="ts">
/**
 * 数据新鲜度面板：进程心跳、宏观序列、行情、各类同步任务。
 * 阈值在后端（dbt 式两级：warn / error），这里只展示后端给的状态与说明，不自己判断新旧。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError } from "../../api/client"
import { fetchFreshness, STATE_LABELS, stateTone, type FreshnessItem, type FreshnessReport } from "../../api/companyData"
import { formatShanghai } from "../../api/health"
import PanelCard from "./PanelCard.vue"
import StateBlock from "./StateBlock.vue"
import StatusPill from "./StatusPill.vue"

const GROUP_LABELS: Record<string, string> = {
  process: "后台进程",
  macro: "宏观序列",
  bars: "行情日线",
  sync: "同步任务",
}

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const report = ref<FreshnessReport | null>(null)

const groups = computed(() => {
  const order = Object.keys(GROUP_LABELS)
  const rank = (key: string): number => (order.includes(key) ? order.indexOf(key) : order.length)
  const map = new Map<string, FreshnessItem[]>()
  for (const item of report.value?.items ?? []) {
    map.set(item.group, [...(map.get(item.group) ?? []), item])
  }
  return [...map.entries()]
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([key, items]) => ({ key, label: GROUP_LABELS[key] ?? key, items }))
})

const problems = computed<number>(
  () => (report.value?.items ?? []).filter((item) => item.state === "stale" || item.state === "missing").length,
)

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    report.value = await fetchFreshness()
    phase.value = report.value ? "ready" : "error"
    failure.value = report.value ? "" : "新鲜度数据格式不正确"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    phase.value = "error"
  }
}

onMounted(load)
</script>

<template>
  <PanelCard title="数据新鲜度" info="阈值按数据频率设定：日线 4 天告警 / 10 天过期，财报 48 小时 / 7 天，宏观按发布频率">
    <template #actions>
      <StatusPill
        v-if="phase === 'ready'"
        :text="problems ? `${problems} 项需要关注` : '全部正常'"
        :tone="problems ? 'danger' : 'ok'"
        dot
      />
      <button class="ghost-btn" type="button" @click="load">刷新</button>
    </template>
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />
    <StateBlock v-else-if="phase === 'error'" state="error" title="无法读取新鲜度" :message="failure" @retry="load" />
    <div v-else class="groups">
      <section v-for="group in groups" :key="group.key">
        <h4>{{ group.label }}</h4>
        <ul>
          <li v-for="item in group.items" :key="`${group.key}-${item.key}`">
            <StatusPill :text="STATE_LABELS[item.state]" :tone="stateTone(item.state)" dot />
            <span class="label">{{ item.label }}</span>
            <span class="detail">{{ item.detail }}</span>
            <span v-if="item.latestDataDate" class="when">数据到 {{ item.latestDataDate }}</span>
            <span v-if="item.lastSyncedAt" class="when">同步于 {{ formatShanghai(item.lastSyncedAt) }}</span>
          </li>
        </ul>
      </section>
    </div>
  </PanelCard>
</template>

<style scoped>
.groups {
  display: grid;
  gap: 16px;
}
h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--muted);
}
ul {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  font-size: 13px;
}
.label {
  color: var(--text-strong);
}
.detail,
.when {
  color: var(--muted);
}
</style>
