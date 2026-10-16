<script setup lang="ts">
/**
 * 宏观判断历史：定时任务每天保存一份快照，这里只读展示。
 * 没有快照时如实说明原因，不拿当前判断冒充历史。
 */
import { onMounted, ref } from "vue"
import { fetchRegimeHistory, type RegimeSnapshotRow } from "../../api/regimeHistory"
import { regimeText } from "../../api/macro"
import PanelCard from "./PanelCard.vue"
import StateBlock from "./StateBlock.vue"

type Phase = "loading" | "ready" | "empty" | "error"

const phase = ref<Phase>("loading")
const rows = ref<RegimeSnapshotRow[]>([])
const notice = ref("")
const errorText = ref("")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const history = await fetchRegimeHistory(30)
    rows.value = history.items
    notice.value = history.notice
    phase.value = history.items.length > 0 ? "ready" : "empty"
  } catch (error) {
    errorText.value = error instanceof Error ? error.message : "读取失败"
    phase.value = "error"
  }
}

function score(value: number | null): string {
  return value === null ? "—" : String(value)
}

onMounted(load)
</script>

<template>
  <PanelCard title="判断历史" info="定时任务每天保存一份当天的判断，规则版本不同的结论分开保存">
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="3" />
    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取判断历史"
      :message="errorText"
      @retry="load"
    />
    <StateBlock
      v-else-if="phase === 'empty'"
      state="empty"
      title="还没有历史判断"
      message="定时任务还没有生成过快照（需要 scheduler 与 worker 在运行）；有了之后这里会按日期列出。"
    />
    <template v-else>
      <table class="history" aria-label="宏观判断历史">
        <thead>
          <tr>
            <th scope="col">日期</th>
            <th scope="col">判断</th>
            <th scope="col">增长</th>
            <th scope="col">通胀</th>
            <th scope="col">流动性</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.asOf}-${row.ruleVersion}`">
            <td>{{ row.asOf }}</td>
            <td>{{ regimeText(row.regimeCode) }}</td>
            <td>{{ score(row.growth) }}</td>
            <td>{{ score(row.inflation) }}</td>
            <td>{{ score(row.liquidity) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="notice" class="note">{{ notice }}</p>
    </template>
  </PanelCard>
</template>

<style scoped>
.history {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.history th,
.history td {
  padding: 6px 8px;
  text-align: left;
  border-bottom: 1px solid var(--line, rgba(255, 255, 255, 0.08));
}

.history td:nth-child(n + 3),
.history th:nth-child(n + 3) {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.note {
  margin: 8px 0 0;
  color: var(--text-muted, #8a94a6);
  font-size: 12px;
}
</style>
