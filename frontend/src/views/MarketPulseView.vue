<script setup lang="ts">
/**
 * 产业研究 · 轮动与情绪（/industry/pulse?tab=rotation|limit|survey）。
 *
 *  - 板块轮动：收盘后入库的板块日线相对沪深300的 RRG 坐标
 *  - 涨停情绪：东财涨停 / 炸板 / 跌停股池
 *  - 机构调研：东财数据中心的调研公告
 *
 * 北向资金按板块的持仓数据自 2024-08-16 起交易所不再按日披露，这里不做。
 */
import { computed } from "vue"
import { useRoute, useRouter } from "vue-router"
import PageHeader from "../components/ui/PageHeader.vue"
import LimitUpPanel from "./parts/pulse/LimitUpPanel.vue"
import RotationPanel from "./parts/pulse/RotationPanel.vue"
import SurveyPanel from "./parts/pulse/SurveyPanel.vue"

type Tab = "rotation" | "limit" | "survey"

const TABS: { key: Tab; label: string }[] = [
  { key: "rotation", label: "板块轮动" },
  { key: "limit", label: "涨停情绪" },
  { key: "survey", label: "机构调研" },
]

const route = useRoute()
const router = useRouter()

const tab = computed<Tab>({
  get: () => {
    const value = route.query.tab
    return TABS.some((item) => item.key === value) ? (value as Tab) : "rotation"
  },
  set: (value) => {
    void router.replace({ query: { ...route.query, tab: value } })
  },
})
</script>

<template>
  <main class="page">
    <PageHeader title="轮动与情绪" subtitle="板块相对强弱的轮动位置、短线涨停情绪和机构调研热度">
      <template #actions>
        <el-radio-group v-model="tab">
          <el-radio-button v-for="item in TABS" :key="item.key" :value="item.key">{{ item.label }}</el-radio-button>
        </el-radio-group>
        <RouterLink class="back" to="/industry">‹ 行业总览</RouterLink>
      </template>
    </PageHeader>

    <RotationPanel v-if="tab === 'rotation'" />
    <LimitUpPanel v-else-if="tab === 'limit'" />
    <SurveyPanel v-else />

    <p class="footnote muted">
      北向资金按行业的持股数据自 2024-08-16 起不再按日披露（交易所调整了陆股通信息披露），所以这里没有「北向按板块」面板。
    </p>
  </main>
</template>

<style scoped>
.back {
  align-self: center;
  color: var(--accent);
  font-size: 13px;
  white-space: nowrap;
}

.footnote {
  margin: 16px 0 0;
  font-size: 12px;
}
</style>
