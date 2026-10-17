<script setup lang="ts">
/** 我的重点关注：读当前账号的自选分组（在「自选关注」页维护），行情 + 近 10 日走势 + 涨停 / 炸板 / 热榜信号。 */
import { computed } from "vue"
import { View } from "@element-plus/icons-vue"
import { companyLink, type WatchOverview } from "../../../api/dashboard"
import { sectionBadge } from "../../../api/industryMarket"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import MiniSpark from "../../../components/ui/MiniSpark.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"

const props = defineProps<{
  watch: WatchOverview | null
  signedIn: boolean
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: []; group: [string] }>()

const VISIBLE = 10

const badge = computed(() => (props.watch ? sectionBadge(props.watch.quoteSection) : null))
const rows = computed(() => props.watch?.rows.slice(0, VISIBLE) ?? [])
const groupId = computed({
  get: () => props.watch?.groupId ?? "",
  set: (value: string) => emit("group", value),
})

function stateTone(state: string): string {
  return state === "强势" ? "rise" : state === "弱势" ? "fall" : state === "震荡" ? "" : "muted"
}
</script>

<template>
  <PanelCard title="我的重点关注" info="按近 5 个交易日涨跌幅分组：≥ +3% 强势、≤ −3% 弱势、其余震荡；信号来自东财涨停 / 炸板 / 跌停池、同花顺热榜与当日 ±5% 异动。" more-text="管理自选" more-to="/watchlist">
    <template #icon><el-icon><View /></el-icon></template>
    <template #actions>
      <StatusPill v-if="badge && watch?.rows.length" :text="badge.text" :tone="badge.tone" dot />
      <el-select v-if="watch && watch.groups.length > 1" v-model="groupId" size="small" style="width: 130px">
        <el-option v-for="group in watch.groups" :key="group.id" :value="group.id" :label="`${group.name}（${group.count}）`" />
      </el-select>
    </template>

    <StateBlock v-if="!signedIn" state="denied" message="登录后显示你在「自选关注」里添加的股票">
      <router-link class="login" to="/login?redirect=/dashboard">去登录 ›</router-link>
    </StateBlock>
    <StateBlock v-else-if="loading && !watch" state="loading" :rows="5" />
    <StateBlock v-else-if="error && !watch" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="watch">
      <StateBlock v-if="!watch.rows.length" state="empty" :message="watch.quoteSection.reason || '这个分组还没有股票，到「自选关注」页面添加后这里会显示行情与信号'" />
      <template v-else>
        <p v-if="watch.quoteSection.status !== 'ok' && watch.quoteSection.reason" class="notice">{{ watch.quoteSection.reason }}</p>
        <table class="dash-table">
          <colgroup>
            <col /><col style="width: 64px" /><col style="width: 60px" /><col style="width: 60px" /><col style="width: 70px" /><col style="width: 84px" />
          </colgroup>
          <thead>
            <tr><th>名称</th><th class="r">最新价</th><th class="r">日涨跌</th><th class="r">近5日</th><th>走势</th><th>信号</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.code">
              <td :title="`${row.name} ${row.code}`">
                <router-link v-if="companyLink(row.code)" :to="companyLink(row.code)">{{ row.name }}</router-link>
                <template v-else>{{ row.name }}</template>
                <span v-if="row.state" class="tag state" :class="stateTone(row.state)">{{ row.state }}</span>
              </td>
              <td class="r"><DeltaValue :value="row.last" :colored="false" /></td>
              <td class="r"><DeltaValue :value="row.changePct" suffix="%" /></td>
              <td class="r"><DeltaValue :value="row.change5dPct" suffix="%" /></td>
              <td>
                <MiniSpark v-if="row.closes.length > 1" :points="row.closes" :tone="(row.change5dPct ?? 0) >= 0 ? 'up' : 'down'" :width="64" :height="20" />
                <span v-else class="faint">—</span>
              </td>
              <td>
                <span v-for="signal in row.signals" :key="signal.text" class="tag" :class="signal.tone">{{ signal.text }}</span>
                <span v-if="!row.signals.length" class="faint">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="watch.sparkSection.status !== 'ok' && watch.sparkSection.reason" class="note">走势：{{ watch.sparkSection.reason }}</p>
        <p v-if="watch.total > rows.length" class="note">共 {{ watch.total }} 只，这里显示前 {{ rows.length }} 只，完整列表见「管理自选」。</p>
      </template>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.state {
  margin-left: 6px;
}

.login {
  color: var(--accent);
  text-decoration: none;
  white-space: nowrap;
}
</style>
