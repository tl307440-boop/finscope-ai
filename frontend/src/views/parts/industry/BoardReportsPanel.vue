<script setup lang="ts">
/** 板块页「行业研报」：东财研报中心近 180 天归档到本板块的研报，不依赖行情接口。 */
import type { Report, Section } from "../../../api/industryMarket"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { sectionLabel, sectionTone } from "./chartKit"

defineProps<{ reports: Report[]; section: Section | null }>()
</script>

<template>
  <PanelCard title="行业研报" info="东方财富研报中心近 180 天归档到本板块的行业研报，点击标题查看原文" flush>
    <template #actions>
      <StatusPill v-if="section" :text="sectionLabel(section.status)" :tone="sectionTone(section.status)" />
    </template>
    <StateBlock v-if="!section" state="loading" :rows="4" />
    <StateBlock v-else-if="!reports.length" state="empty" :message="section.reason || '暂无研报'" />
    <ul v-else class="report-list">
      <li v-for="item in reports" :key="item.infoCode">
        <a :href="item.url" target="_blank" rel="noreferrer" class="report-title">{{ item.title }}</a>
        <p class="muted small">
          <span class="tabular">{{ item.publishedOn }}</span> · {{ item.orgName }}<template v-if="item.researcher"> · {{ item.researcher }}</template>
          <StatusPill v-if="item.rating" :text="item.rating" tone="accent" />
        </p>
      </li>
    </ul>
  </PanelCard>
</template>

<style scoped>
.report-list {
  list-style: none;
  margin: 0;
  padding: 0 16px;
}

.report-list li {
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}

.report-title {
  color: var(--text-strong);
  font-weight: 600;
  text-decoration: none;
  font-size: 14px;
}

.report-title:hover {
  color: var(--accent);
}

.report-list p {
  margin: 4px 0 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.small {
  font-size: 12px;
}
</style>
