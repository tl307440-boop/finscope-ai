<script setup lang="ts">
/** 数据源清单与质量：每个来源的最新日期、行数、待审行数，以及最近被闸门标记的问题。 */
import { onBeforeUnmount, onMounted, ref } from "vue"
import { ApiError } from "../../../api/client"
import { fetchSources, type DataStatus, type SourcesView } from "../../../api/customAnalysis"
import { formatMoment } from "../../../api/json"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const view = ref<SourcesView | null>(null)
let controller: AbortController | null = null

async function load(): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  phase.value = "loading"
  failure.value = ""
  try {
    view.value = await fetchSources(controller.signal)
    phase.value = "ready"
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError") {
      return
    }
    failure.value = caught instanceof ApiError ? caught.message : "无法连接数据服务"
    phase.value = "error"
  }
}

const STATUS_TEXT: Record<DataStatus, string> = {
  ok: "正常",
  stale: "已过期",
  unavailable: "无数据",
}

onMounted(() => void load())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <div class="sources">
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else-if="view">
      <PanelCard title="数据源清单" info="只用交易所与指数公司的公开页面，走限速网关；失败的来源不会用估算值补洞">
        <StateBlock v-if="!view.sources.length" state="empty" message="还没有登记任何数据源" />
        <table v-else class="table">
          <thead>
            <tr>
              <th>来源</th>
              <th>状态</th>
              <th>区间</th>
              <th>行数</th>
              <th>待审</th>
              <th>抓取时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="source in view.sources" :key="source.key">
              <td>
                <a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer">{{
                  source.label
                }}</a>
                <span v-else>{{ source.label }}</span>
                <div class="muted fine">{{ source.provider }} · {{ source.frequency }}</div>
                <div v-if="source.note" class="muted fine">{{ source.note }}</div>
              </td>
              <td>
                <span class="badge" :class="`s-${source.status}`">{{ STATUS_TEXT[source.status] }}</span>
              </td>
              <td class="tabular">
                {{ source.firstDate ?? "—" }}<br />~ {{ source.lastDate ?? "—" }}
              </td>
              <td class="tabular">{{ source.rows }}</td>
              <td class="tabular" :class="{ warn: source.reviewRows > 0 }">{{ source.reviewRows }}</td>
              <td class="tabular">{{ formatMoment(source.fetchedAt) }}</td>
            </tr>
          </tbody>
        </table>
      </PanelCard>

      <PanelCard title="近期质量问题" info="质量闸门标成「待审」的行：数值已入库，但与交易所口径对不上或单日变化过大">
        <StateBlock v-if="!view.recentIssues.length" state="empty" message="最近没有被标记的数据" />
        <ul v-else class="issues">
          <li v-for="(issue, index) in view.recentIssues" :key="`${issue.tradeDate}-${issue.subject}-${index}`">
            <span class="tabular">{{ issue.tradeDate }}</span>
            <strong>{{ issue.subject }}</strong>
            <span class="muted">{{ issue.note }}</span>
          </li>
        </ul>
      </PanelCard>

      <p class="muted fine">规则版本 {{ view.rulesVersion || "—" }}</p>
    </template>
  </div>
</template>

<style scoped>
.sources {
  display: grid;
  gap: var(--gap);
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.table th,
.table td {
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--line-soft);
}

.table th {
  font-weight: 500;
  color: var(--muted);
}

.fine {
  font-size: 12px;
  margin: 2px 0 0;
}

.warn {
  color: var(--warn);
  font-weight: 600;
}

.badge {
  display: inline-block;
  padding: 1px 8px;
  font-size: 12px;
  border: 1px solid currentcolor;
}

.s-ok {
  color: var(--ok);
}

.s-stale {
  color: var(--warn);
}

.s-unavailable {
  color: var(--muted);
}

.issues {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.issues li {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
}
</style>
