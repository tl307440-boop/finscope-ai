<script setup lang="ts">
/**
 * 研究板（`/research/board`）。
 *
 * 三块内容各自独立取数、各自报错，互不阻塞：
 * - `GET /api/v1/market/hotspots`：合规公告热度（可以关闭数据源，关闭时 label 为「无法判断」并带 notice）；
 * - `GET /api/v1/macro/china`：每条中国宏观序列的真实落库状态（多数情况是 unavailable）；
 * - `GET /api/v1/me/digest`：登录后的站内每日摘要。
 * 三者在当前环境经常是空的，所以每块都给出后端原文的空状态，不用占位数字填满版面。
 */
import { onMounted, ref } from "vue"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"
import MetricTile from "../components/ui/MetricTile.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"

const session = useSessionStore()

const phase = ref<Phase>("loading")
const failure = ref("")
const requestId = ref("")
const hotspot = ref("")
const hotspotNotice = ref("")
const hotspotCount = ref<number | null>(null)
const hotspotTopics = ref<{ label: string; count: number }[]>([])
const china = ref<string[]>([])
const chinaNotice = ref("")
const digest = ref<string[]>([])
const digestFailure = ref("")
const digestPhase = ref<Phase>("loading")

/** 三块各自独立取数，任一块失败都只影响它自己。 */
async function loadHotspots(): Promise<void> {
  const response = await fetch(apiUrl("/api/v1/market/hotspots"), {
    headers: { Accept: "application/json" },
    credentials: "include",
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const data = asRecord(asRecord(body)?.data)
  hotspot.value = asString(data?.label) ?? ""
  hotspotNotice.value = asString(data?.notice) ?? ""
  hotspotCount.value = asNumber(data?.count)
  const topics = data?.topics
  hotspotTopics.value = Array.isArray(topics)
    ? topics.flatMap((item) => {
        const row = asRecord(item)
        const label = asString(row?.label)
        if (!label) {
          return []
        }
        return [{ label, count: asNumber(row?.count) ?? 0 }]
      })
    : []
}

async function loadChina(): Promise<void> {
  const response = await fetch(apiUrl("/api/v1/macro/china"), {
    headers: { Accept: "application/json" },
    credentials: "include",
  })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const data = asRecord(asRecord(body)?.data)
  chinaNotice.value = asString(data?.notice) ?? ""
  const series = data?.series
  china.value = Array.isArray(series)
    ? series.flatMap((item) => {
        const row = asRecord(item)
        const name = asString(row?.name)
        const status = asString(row?.status)
        return name && status ? [`${name}：${status === "unavailable" ? "不可用" : status}`] : []
      })
    : []
}

async function loadDigest(): Promise<void> {
  digestPhase.value = "loading"
  digestFailure.value = ""
  if (!session.accessToken) {
    digestPhase.value = "ready"
    digest.value = []
    return
  }
  try {
    const response = await authorizedFetch(
      "/api/v1/me/digest",
      { method: "GET" },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const items = asRecord(asRecord(body)?.data)?.items
    digest.value = Array.isArray(items)
      ? items.flatMap((item) => {
          const row = asRecord(item)
          const title = asString(row?.title)
          const summary = asString(row?.summary)
          return title && summary ? [`${title}：${summary}`] : []
        })
      : []
    digestPhase.value = "ready"
  } catch (caught) {
    digestFailure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    digestPhase.value = "error"
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    await loadHotspots()
    await loadChina()
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
  await loadDigest()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="研究板"
      subtitle="公告热度、中国宏观序列状态与站内摘要；三块各自取数，空就是空"
    >
      <template #actions>
        <button class="retry" type="button" :disabled="phase === 'loading'" @click="load">刷新</button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <template v-else-if="phase === 'error'">
      <StateBlock state="error" :message="failure" @retry="load" />
      <p v-if="requestId" class="muted request-line">请求号 {{ requestId }}</p>
    </template>

    <template v-else>
      <div class="grid-1-2">
        <PanelCard title="公告热度" info="GET /api/v1/market/hotspots · 合规公告聚合">
          <div class="heat">
            <MetricTile
              label="热度"
              :value="hotspot || '无法判断'"
              :hint="hotspotCount === null ? '后端未给出条数' : `收录 ${hotspotCount} 条`"
            />
            <StatusPill :tone="hotspotCount ? 'accent' : 'warn'" :text="hotspot || '暂无热度'" />
          </div>
          <p v-if="!hotspotCount" class="muted heat-empty">
            {{ hotspotNotice || "该时间窗内没有收录到合规公告事件。" }}
          </p>
          <p v-else-if="hotspotNotice" class="muted heat-empty">{{ hotspotNotice }}</p>
          <ul v-if="hotspotTopics.length" class="topic-list">
            <li v-for="topic in hotspotTopics" :key="topic.label">
              {{ topic.label }} <span class="tabular">{{ topic.count }}</span> 条
            </li>
          </ul>
        </PanelCard>

        <PanelCard title="中国宏观" info="GET /api/v1/macro/china · 每条序列的真实落库状态">
          <p v-if="!china.length" class="muted">
            后端没有返回任何序列状态。{{ chinaNotice }}
          </p>
          <ul v-else class="line-list">
            <li v-for="item in china" :key="item">
              <StatusPill
                :tone="item.includes('不可用') ? 'warn' : 'accent'"
                :text="item.split('：')[0] ?? item"
              />
              <span class="line-text">{{ item.split("：").slice(1).join("：") || item }}</span>
            </li>
          </ul>
        </PanelCard>
      </div>

      <div class="grid-2">
        <PanelCard title="站内摘要" info="GET /api/v1/me/digest · 需要登录">
          <div v-if="digestPhase === 'loading'"><StateBlock state="loading" :rows="2" /></div>
          <StateBlock
            v-else-if="digestPhase === 'error'"
            state="error"
            :message="digestFailure"
            @retry="loadDigest"
          />
          <p v-else-if="!session.accessToken" class="muted">
            登录后可以看今天的站内摘要。摘要只包含已保存的研究记录标题与近 24 小时的公告条数，
            不做邮件或推送。
          </p>
          <ul v-else-if="digest.length" class="line-list">
            <li v-for="item in digest" :key="item">
              <strong>{{ item.split("：")[0] }}</strong>
              <span class="line-text">{{ item.split("：").slice(1).join("：") }}</span>
            </li>
          </ul>
          <p v-else class="muted">后端返回的摘要列表为空。</p>
        </PanelCard>

        <PanelCard title="使用边界" info="三块内容的真实边界">
          <ul class="line-list">
            <li>公告热度只在合规数据源开启时统计；关闭时热度写「无法判断」，不补历史条数。</li>
            <li>宏观序列多数处于 <code>unavailable</code>，表示尚未同步，不代表数值为零。</li>
            <li>站内摘要只读已保存的报告标题与公告条数，不生成投资建议。</li>
          </ul>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="'—'"
        :sources="['合规公告事件（已入库）', '中国宏观序列状态', '站内摘要']"
        :note="chinaNotice || '任一数据源为空时按空状态展示，不使用占位数字'"
      />
    </template>
  </main>
</template>

<style scoped>
.heat {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.heat-empty {
  margin: 8px 0 0;
  font-size: 12px;
}

.request-line {
  margin: 8px 0 0;
  font-size: 12px;
}

.topic-list,
.line-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.line-list li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.line-text {
  color: var(--text);
}

code {
  font-size: 12px;
  color: var(--accent);
}
</style>
