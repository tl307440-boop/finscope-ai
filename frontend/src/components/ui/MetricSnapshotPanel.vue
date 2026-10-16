<script setup lang="ts">
/**
 * 体检快照：后台定时任务按真实披露算出的指标，每项都能追溯到披露来源。
 * 没有真实披露、或还没跑到这家公司时，如实显示空状态，不拿样例补。
 */
import { ref, watch } from "vue"
import { fetchMetricSnapshot, formatMetric, type MetricSnapshot } from "../../api/metricSnapshot"
import PanelCard from "./PanelCard.vue"
import StateBlock from "./StateBlock.vue"

const props = defineProps<{ instrumentId: string }>()

type Phase = "loading" | "ready" | "empty" | "error"

const phase = ref<Phase>("loading")
const snapshot = ref<MetricSnapshot | null>(null)
const errorText = ref("")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    snapshot.value = await fetchMetricSnapshot(props.instrumentId)
    phase.value = snapshot.value.status
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : "读取失败"
    phase.value = "error"
  }
}

watch(() => props.instrumentId, load, { immediate: true })
</script>

<template>
  <PanelCard title="体检快照" info="后台每天按已披露的财报事实计算；算不出来的指标写明原因，不拿样例补">
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="3" />
    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取体检快照"
      :message="errorText"
      @retry="load"
    />
    <StateBlock
      v-else-if="phase === 'empty'"
      state="empty"
      title="还没有体检快照"
      :message="snapshot?.notice || '可能尚无真实财报，或定时任务还没跑到这家公司。'"
    />
    <ul v-else class="snapshot">
      <li v-for="item in snapshot?.items ?? []" :key="item.code">
        <div class="name">{{ item.label }}</div>
        <div class="value" :class="{ dim: item.value === null }">{{ formatMetric(item) }}</div>
        <div class="meta">
          <template v-if="item.value !== null">
            {{ item.periodType }} · 截至 {{ item.periodEnd }}
            <a v-if="item.sources[0]" :href="item.sources[0]" target="_blank" rel="noopener noreferrer">来源</a>
          </template>
          <template v-else>{{ item.reason ?? "暂无数据" }}</template>
        </div>
      </li>
    </ul>
  </PanelCard>
</template>

<style scoped>
.snapshot {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.snapshot li {
  padding: 8px 10px;
  border: 1px solid var(--line, rgba(255, 255, 255, 0.08));
  border-radius: 6px;
}

.name {
  font-size: 12px;
  color: var(--text-muted, #8a94a6);
}

.value {
  margin: 2px 0;
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.value.dim {
  color: var(--text-muted, #8a94a6);
}

.meta {
  font-size: 11px;
  color: var(--text-muted, #8a94a6);
}

.meta a {
  margin-left: 6px;
}
</style>
