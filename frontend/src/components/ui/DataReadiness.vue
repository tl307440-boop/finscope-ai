<script setup lang="ts">
/**
 * 数据就绪度提示条：这家公司的财报 / 日线有没有、新不新、是不是正在后台抓取。
 * 只陈述后端返回的状态，不编造进度；全部就绪且最新时不渲染。
 */
import { computed } from "vue"
import { STATE_LABELS, stateTone, type CompanyDataStatus, type DatasetStatus } from "../../api/companyData"
import StatusPill from "./StatusPill.vue"

const props = defineProps<{
  status: CompanyDataStatus | null
  /** 是否已登录：未登录时无法触发后台抓取 */
  signedIn: boolean
  /** 轮询超时后仍未完成 */
  timedOut?: boolean
}>()

defineEmits<{ request: [] }>()

interface Row {
  key: string
  text: string
  pill: string
  tone: "ok" | "warn" | "danger" | "muted"
}

function describe(item: DatasetStatus): Row | null {
  if (item.sync === "queued" || item.sync === "running") {
    return {
      key: item.dataset,
      text: `${item.label}：${item.sync === "queued" ? "已排队" : "正在抓取"}（${item.source}）`,
      pill: "抓取中",
      tone: "warn",
    }
  }
  if (item.state === "fresh") {
    return null
  }
  const date = item.latestDataDate ? `，最新到 ${item.latestDataDate}` : ""
  const extra = item.message ? `：${item.message}` : ""
  return {
    key: item.dataset,
    text: `${item.label}（${item.source}）${STATE_LABELS[item.state]}${date}${extra}`,
    pill: STATE_LABELS[item.state],
    tone: stateTone(item.state),
  }
}

const rows = computed<Row[]>(() => (props.status?.datasets ?? []).flatMap((item) => describe(item) ?? []))
const canAsk = computed<boolean>(() => (props.status?.datasets ?? []).some((item) => item.canRequest))
</script>

<template>
  <section v-if="rows.length" class="readiness" role="status" aria-live="polite">
    <ul>
      <li v-for="row in rows" :key="row.key">
        <StatusPill :text="row.pill" :tone="row.tone" dot />
        <span>{{ row.text }}</span>
      </li>
    </ul>
    <p v-if="timedOut" class="hint">抓取仍在进行，稍后刷新页面即可查看；数据到位前页面只显示已入库的内容。</p>
    <p v-else-if="canAsk && !signedIn" class="hint">登录后访问本页会自动登记后台抓取，完成后刷新即可。</p>
    <button v-else-if="canAsk" class="ghost-btn" type="button" @click="$emit('request')">立即抓取</button>
  </section>
</template>

<style scoped>
.readiness {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  font-size: 13px;
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
  align-items: center;
  gap: 8px;
}
.hint {
  margin: 0;
  color: var(--muted);
}
</style>
