<script setup lang="ts">
/** 热点股票：同花顺 1 小时热榜 + 腾讯行情 / 新浪全市场涨幅榜（剔除新股与小成交额）。 */
import { computed, ref } from "vue"
import { Star } from "@element-plus/icons-vue"
import { companyLink, type HotStock, type StocksOverview } from "../../../api/dashboard"
import { sectionBadge } from "../../../api/industryMarket"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { yuan } from "../industry/chartKit"

const props = defineProps<{
  stocks: StocksOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: [] }>()

const mode = ref<"hot" | "gain">("hot")
const section = computed(() => (props.stocks ? (mode.value === "hot" ? props.stocks.hotSection : props.stocks.gainSection) : null))
const badge = computed(() => (section.value ? sectionBadge(section.value) : null))
const rows = computed<HotStock[]>(() => (props.stocks ? (mode.value === "hot" ? props.stocks.hot : props.stocks.gainers).slice(0, 8) : []))

function peText(value: number | null): string {
  if (value === null) {
    return "—"
  }
  return value < 0 ? "亏损" : value.toFixed(1)
}
</script>

<template>
  <PanelCard title="热点股票" info="人气热股：同花顺 1 小时热榜，价格与市盈率(TTM) 来自腾讯行情；涨幅居前：新浪沪深京 A 股涨幅榜，剔除 N / C 开头的新股和成交额不足 3 亿元的个股。">
    <template #icon><el-icon><Star /></el-icon></template>
    <template #actions>
      <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="hot">人气热股</el-radio-button>
        <el-radio-button value="gain">涨幅居前</el-radio-button>
      </el-radio-group>
    </template>

    <StateBlock v-if="loading && !stocks" state="loading" :rows="6" />
    <StateBlock v-else-if="error && !stocks" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="stocks && section">
      <p v-if="section.status !== 'ok' && section.reason" class="notice">{{ section.reason }}</p>
      <StateBlock v-if="!rows.length" state="empty" :message="section.reason || '暂无数据'" />
      <table v-else class="dash-table">
        <colgroup>
          <col style="width: 24%" /><col style="width: 64px" /><col style="width: 70px" /><col style="width: 70px" /><col style="width: 78px" /><col />
        </colgroup>
        <thead>
          <tr>
            <th>股票名称</th>
            <th>代码</th>
            <th class="r">最新价</th>
            <th class="r">涨跌幅</th>
            <th class="r">{{ mode === "hot" ? "市盈率(TTM)" : "成交额" }}</th>
            <th>{{ mode === "hot" ? "标签" : "换手率" }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.code">
            <td :title="row.concepts.join('、')">
              <span v-if="row.rank" class="rank num">{{ row.rank }}</span>
              <router-link v-if="companyLink(row.code)" :to="companyLink(row.code)">{{ row.name }}</router-link>
              <template v-else>{{ row.name }}</template>
            </td>
            <td class="num faint">{{ row.code }}</td>
            <td class="r"><DeltaValue :value="row.last" :colored="false" /></td>
            <td class="r"><DeltaValue :value="row.changePct" suffix="%" /></td>
            <td class="r num">{{ mode === "hot" ? peText(row.peTtm) : yuan(row.amount, 1) }}</td>
            <td>
              <template v-if="mode === 'hot'">
                <span v-if="row.tag" class="tag" :class="{ rise: row.tag.includes('板') }">{{ row.tag }}</span>
                <span v-else-if="row.concepts.length" class="tag muted">{{ row.concepts[0] }}</span>
              </template>
              <span v-else class="num">{{ row.turnoverPct === null ? "—" : `${row.turnoverPct.toFixed(2)}%` }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="note">{{ mode === "hot" ? stocks.rules[0] : stocks.rules[1] }}</p>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.rank {
  display: inline-block;
  min-width: 16px;
  margin-right: 4px;
  color: var(--orange);
  font-size: 12px;
}
</style>
