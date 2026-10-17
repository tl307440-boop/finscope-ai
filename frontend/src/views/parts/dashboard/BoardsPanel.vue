<script setup lang="ts">
/** 热点板块：board-heat-v1 热度指数（同花顺热门板块）/ 新浪行业与概念板块当日涨幅榜。 */
import { computed, ref } from "vue"
import { Grid } from "@element-plus/icons-vue"
import type { BoardsOverview } from "../../../api/dashboard"
import { sectionBadge } from "../../../api/industryMarket"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { heatBadge } from "./charts"
import { yuan } from "../industry/chartKit"

const props = defineProps<{
  boards: BoardsOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: []; basis: [] }>()

const mode = ref<"heat" | "gain">("heat")
const section = computed(() => (props.boards ? (mode.value === "heat" ? props.boards.heatSection : props.boards.gainSection) : null))
const badge = computed(() => (section.value ? sectionBadge(section.value) : null))
const heatRows = computed(() => props.boards?.boards.slice(0, 8) ?? [])
const gainRows = computed(() => props.boards?.gainers.slice(0, 8) ?? [])
</script>

<template>
  <PanelCard title="热点板块" info="热度指数 = 0.4×同花顺热度分 + 0.3×当日涨幅分位 + 0.3×涨停家数分（10 家封顶）；≥60 且当日涨幅 ≥1% 记为热点。点「口径」看逐项得分。">
    <template #icon><el-icon><Grid /></el-icon></template>
    <template #actions>
      <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="heat">按热度</el-radio-button>
        <el-radio-button value="gain">按涨幅</el-radio-button>
      </el-radio-group>
      <button type="button" class="link" @click="emit('basis')">口径 ›</button>
    </template>

    <StateBlock v-if="loading && !boards" state="loading" :rows="6" />
    <StateBlock v-else-if="error && !boards" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="boards && section">
      <p v-if="section.status !== 'ok' && section.reason" class="notice">{{ section.reason }}</p>
      <template v-if="mode === 'heat'">
        <StateBlock v-if="!heatRows.length" state="empty" :message="section.reason || '同花顺热门板块暂无数据'" />
        <table v-else class="dash-table">
          <colgroup><col style="width: 34px" /><col /><col style="width: 64px" /><col style="width: 70px" /><col style="width: 42%" /></colgroup>
          <thead>
            <tr><th>排名</th><th>板块</th><th class="r">热度指数</th><th class="r">今日涨幅</th><th>领涨 / 标签</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in heatRows" :key="row.code">
              <td class="num">{{ index + 1 }}</td>
              <td :title="row.name">{{ row.name }}<span class="sub">{{ row.kind }}</span></td>
              <td class="r"><span class="heat num" :style="{ background: heatBadge(row.index) }">{{ row.index }}</span></td>
              <td class="r"><DeltaValue :value="row.changePct" suffix="%" /></td>
              <td>
                <template v-if="row.leaderName">{{ row.leaderName }} <DeltaValue :value="row.leaderChangePct" suffix="%" size="sm" /> </template>
                <span v-if="row.limitUps" class="tag rise">{{ row.limitUps }}家涨停</span>
                <span v-if="row.hotTag" class="tag warn">{{ row.hotTag }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
      <template v-else>
        <StateBlock v-if="!gainRows.length" state="empty" :message="section.reason || '新浪板块涨幅榜暂无数据'" />
        <table v-else class="dash-table">
          <colgroup><col style="width: 34px" /><col /><col style="width: 70px" /><col style="width: 78px" /><col style="width: 38%" /></colgroup>
          <thead>
            <tr><th>排名</th><th>板块</th><th class="r">今日涨幅</th><th class="r">成交额</th><th>领涨股</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in gainRows" :key="row.code">
              <td class="num">{{ index + 1 }}</td>
              <td :title="`${row.name}（${row.members} 只成分）`">{{ row.name }}<span class="sub">{{ row.kind }}</span></td>
              <td class="r"><DeltaValue :value="row.changePct" suffix="%" /></td>
              <td class="r num">{{ yuan(row.amount, 0) }}</td>
              <td>
                <template v-if="row.leaderName">{{ row.leaderName }} <DeltaValue :value="row.leaderChangePct" suffix="%" size="sm" /></template>
                <span v-else class="faint">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </template>
      <p class="note">{{ mode === "heat" ? boards.rules[1] : boards.rules[2] }}</p>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.heat {
  display: inline-block;
  min-width: 34px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 600;
  color: #fff;
  text-align: center;
}

.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.link:hover {
  color: var(--accent);
}
</style>
