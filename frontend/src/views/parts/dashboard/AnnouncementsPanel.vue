<script setup lang="ts">
/** 市场公告：巨潮资讯全市场检索，后端按标题规则分级、按热榜 / 涨跌停 / 涨幅榜给热度。 */
import { computed, ref } from "vue"
import { Bell } from "@element-plus/icons-vue"
import type { Announcement, AnnouncementsOverview } from "../../../api/dashboard"
import { sectionBadge } from "../../../api/industryMarket"
import PanelCard from "../../../components/ui/PanelCard.vue"
import SectionTabs from "../../../components/ui/SectionTabs.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"

const props = defineProps<{
  announcements: AnnouncementsOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: [] }>()

const TABS = [
  { key: "important", label: "重要公告" },
  { key: "hot", label: "热门公告" },
] as const

const mode = ref<string>("important")
const badge = computed(() => (props.announcements ? sectionBadge(props.announcements.section) : null))
const rows = computed<Announcement[]>(() =>
  props.announcements ? (mode.value === "hot" ? props.announcements.hot : props.announcements.important).slice(0, 8) : [],
)
const topCounts = computed(() => props.announcements?.counts.slice(0, 4) ?? [])

/** 巨潮只给到日期（北京时间零点）时只显示日期。 */
function when(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return "—"
  }
  const pad = (part: number): string => String(part).padStart(2, "0")
  const day = `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`
  return parsed.getHours() === 0 && parsed.getMinutes() === 0 ? day : `${day} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
}

function strength(row: Announcement): number {
  return mode.value === "hot" ? (row.heatLevel ?? 0) : row.level
}
</script>

<template>
  <PanelCard title="市场公告" info="巨潮资讯（证监会指定信息披露平台）全市场检索，窗口为上一工作日至今。重要度只看标题关键词；热门 = 公告主体在热榜前列或当日涨跌停 / 涨幅居前。">
    <template #icon><el-icon><Bell /></el-icon></template>
    <template #actions>
      <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
    </template>

    <StateBlock v-if="loading && !announcements" state="loading" :rows="6" />
    <StateBlock v-else-if="error && !announcements" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="announcements">
      <div class="bar">
        <SectionTabs v-model="mode" :tabs="TABS" />
        <span class="counts">
          {{ announcements.windowStart.slice(5) }} 至 {{ announcements.windowEnd.slice(5) }} · 扫描 {{ announcements.scanned }} 条
          <template v-for="item in topCounts" :key="item.name"> · {{ item.name }} {{ item.count }}</template>
        </span>
      </div>
      <p v-if="announcements.section.status !== 'ok' && announcements.section.reason" class="notice">{{ announcements.section.reason }}</p>
      <StateBlock
        v-if="!rows.length"
        state="empty"
        :message="mode === 'hot' ? '当前窗口里没有热门标的（热榜 / 涨跌停 / 涨幅榜）的公告' : '当前窗口里没有命中重要度规则的公告'"
      />
      <table v-else class="dash-table">
        <colgroup><col style="width: 56px" /><col /><col style="width: 68px" /><col style="width: 50px" /></colgroup>
        <thead>
          <tr><th>日期</th><th>公司 / 标题</th><th>类型</th><th>{{ mode === "hot" ? "热度" : "重要度" }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id" :title="row.heatReason ? `${row.title}\n热度依据：${row.heatReason}` : row.title">
            <td class="num faint">{{ when(row.publishedAt) }}</td>
            <td>
              <span class="company">{{ row.name }}</span>
              <a v-if="row.url" :href="row.url" target="_blank" rel="noopener noreferrer">{{ row.title }}</a>
              <template v-else>{{ row.title }}</template>
            </td>
            <td><span class="tag" :class="row.tone">{{ row.kind }}</span></td>
            <td>
              <span class="level" :aria-label="`${strength(row)} 级`">
                <i v-for="step in 3" :key="step" :class="{ on: step <= strength(row) }" />
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="note">{{ mode === "hot" ? announcements.rules[2] : announcements.rules[1] }}</p>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
}

.bar > :first-child {
  flex: none;
  flex-wrap: nowrap;
}

.counts {
  min-width: 0;
  margin-left: auto;
  color: var(--faint);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.company {
  margin-right: 6px;
  color: var(--muted);
}

.level {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 14px;
}

.level i {
  display: block;
  width: 5px;
  border-radius: 1px;
  background: #334155;
}

.level i:nth-child(1) {
  height: 6px;
}

.level i:nth-child(2) {
  height: 10px;
}

.level i:nth-child(3) {
  height: 14px;
}

.level i.on {
  background: var(--orange);
}
</style>
