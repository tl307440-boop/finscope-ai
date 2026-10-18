<script setup lang="ts">
/**
 * 数据中心：中国宏观（人民银行统计表）月度导入提醒与导入入口，仅管理员可见的页面里使用。
 *
 *   GET  /api/v1/macro/china               → 三项序列的最新观测日
 *   POST /api/v1/macro/china/observations  → { csv_text } 导入（管理员）
 *
 * 不做自动抓取：人民银行官网 robots.txt 不允许，所以这里只提醒、给出官方页面链接并接收 CSV。
 */
import { computed, onMounted, ref } from "vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../../../api/client"
import { asNumber, asRecord, asString, listOf } from "../../../api/json"
import {
  CSV_COLUMNS,
  PBOC_LINKS,
  PBOC_SERIES,
  importStatus,
  type ImportState,
  type ImportStatus,
} from "../../../api/chinaImport"
import { restoreSession, useSessionStore } from "../../../stores/auth"

const props = defineProps<{ now?: Date }>()

const session = useSessionStore()

const latestByCode = ref<Record<string, string | null>>({})
const loadPhase = ref<"loading" | "ready" | "error">("loading")
const loadText = ref("")

const file = ref<File | null>(null)
const importPhase = ref<"idle" | "loading" | "done" | "error">("idle")
const importText = ref("")
const importErrors = ref<string[]>([])

const MAX_ERRORS_SHOWN = 5

function today(): Date {
  return props.now ?? new Date()
}

const rows = computed(() =>
  PBOC_SERIES.map((series) => ({
    ...series,
    status: importStatus(latestByCode.value[series.code], today()),
  })),
)

const pending = computed(() => rows.value.filter((row) => row.status.state !== "current"))

const expectedText = computed(() => importStatus(null, today()).expected)

function tone(state: ImportState): "ok" | "warn" | "danger" {
  return state === "current" ? "ok" : state === "due" ? "warn" : "danger"
}

function statusOf(row: { status: ImportStatus }): string {
  return row.status.text
}

function authRetry(): () => Promise<string | null> {
  return async () => {
    const restored = await restoreSession()
    return restored ? session.accessToken : null
  }
}

async function loadStatus(): Promise<void> {
  loadPhase.value = "loading"
  loadText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/macro/china",
      { method: "GET" },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const latest: Record<string, string | null> = {}
    for (const record of listOf(data?.series)) {
      const code = asString(record.code)
      if (code) {
        latest[code] = asString(record.latest_observation_date)
      }
    }
    latestByCode.value = latest
    loadPhase.value = "ready"
  } catch (caught) {
    loadText.value = caught instanceof ApiError ? caught.message : "无法读取序列状态"
    loadPhase.value = "error"
  }
}

function pick(event: Event): void {
  const input = event.target as HTMLInputElement
  file.value = input.files && input.files.length > 0 ? input.files[0] : null
  importText.value = ""
  importErrors.value = []
  importPhase.value = "idle"
}

function readText(source: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "")
    reader.onerror = () => reject(new Error("无法读取所选文件"))
    reader.readAsText(source, "utf-8")
  })
}

async function runImport(): Promise<void> {
  if (!file.value) {
    importPhase.value = "error"
    importText.value = "请先选择 CSV 文件"
    return
  }
  importPhase.value = "loading"
  importText.value = ""
  importErrors.value = []
  try {
    const text = (await readText(file.value)).replace(/^\uFEFF/, "")
    if (!text.trim()) {
      throw new Error("文件是空的")
    }
    const response = await authorizedFetch(
      "/api/v1/macro/china/observations",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv_text: text }),
      },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const accepted = asNumber(data?.accepted) ?? 0
    const duplicates = asNumber(data?.duplicate_count) ?? 0
    const rawErrors = data?.errors
    const messages = Array.isArray(rawErrors)
      ? rawErrors.filter((item): item is string => typeof item === "string")
      : []
    importErrors.value = messages.slice(0, MAX_ERRORS_SHOWN)
    importText.value =
      `入库 ${accepted} 条，重复 ${duplicates} 条，共 ${messages.length} 条说明` +
      (messages.length > MAX_ERRORS_SHOWN ? `（只显示前 ${MAX_ERRORS_SHOWN} 条）` : "")
    importPhase.value = accepted > 0 ? "done" : "error"
    if (accepted > 0) {
      await loadStatus()
    }
  } catch (caught) {
    importText.value =
      caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "导入失败，请稍后重试"
    importPhase.value = "error"
  }
}

onMounted(() => {
  void loadStatus()
})
</script>

<template>
  <PanelCard
    title="中国宏观 · 人民银行月度导入"
    info="社融存量、政府债券净融资、企业中长期贷款来自人民银行调查统计司统计表。对方 robots.txt 不允许抓取，系统不做自动同步，需要每月手工导入"
  >
    <div
      class="remind"
      :class="{ ok: loadPhase === 'ready' && pending.length === 0 }"
      role="status"
      data-testid="china-import-remind"
    >
      <template v-if="loadPhase === 'loading'">正在读取序列状态…</template>
      <template v-else-if="loadPhase === 'error'">
        {{ loadText }}。人民银行一般在每月 12–15 日前后发布上月数据，请到下方官方页面核对并导入。
      </template>
      <template v-else-if="pending.length">
        请导入：{{ pending.map((row) => row.label).join("、") }} 还没有 {{ expectedText }} 的数据。
        人民银行一般在每月 12–15 日前后发布上月数据。
      </template>
      <template v-else>三项都已是最新（应有 {{ expectedText }}）。下月 12–15 日前后会有新数据。</template>
    </div>

    <ul class="series-list" aria-label="需要手工导入的序列">
      <li v-for="row in rows" :key="row.code">
        <div class="series-main">
          <span class="cell-strong">{{ row.label }}</span>
          <span class="cell-sub">{{ row.table }}</span>
        </div>
        <StatusPill
          v-if="loadPhase === 'ready'"
          :text="statusOf(row)"
          :tone="tone(row.status.state)"
          dot
        />
        <span v-else class="muted">—</span>
      </li>
    </ul>

    <h3 class="sub">人民银行官方页面（在你自己的浏览器里打开，核对后整理成 CSV）</h3>
    <ul class="links">
      <li v-for="link in PBOC_LINKS" :key="link.url">
        <a :href="link.url" target="_blank" rel="noopener noreferrer">{{ link.label }}</a>
      </li>
    </ul>

    <h3 class="sub">导入 CSV</h3>
    <form class="import-form" @submit.prevent="runImport">
      <input
        type="file"
        accept=".csv,text/csv"
        aria-label="选择 CSV 文件"
        @change="pick"
      />
      <button type="submit" class="import-btn" :disabled="importPhase === 'loading'">
        {{ importPhase === "loading" ? "导入中…" : "导入" }}
      </button>
    </form>
    <p class="card-note">
      必须包含列：<code>{{ CSV_COLUMNS }}</code>。观测日取每月 1 日，公布日填人民银行发布日期；
      重复行会被跳过，不合规行会逐条列出，不会补齐或估算任何数值。
    </p>
    <p
      v-if="importText"
      class="inline-note"
      :class="{ danger: importPhase === 'error' }"
      role="status"
      data-testid="china-import-result"
    >
      {{ importText }}
    </p>
    <ul v-if="importErrors.length" class="errors">
      <li v-for="message in importErrors" :key="message">{{ message }}</li>
    </ul>
  </PanelCard>
</template>

<style scoped>
.remind {
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
  background: color-mix(in srgb, var(--warn, #e6a23c) 14%, transparent);
  border: 1px solid color-mix(in srgb, var(--warn, #e6a23c) 45%, transparent);
}

.remind.ok {
  background: color-mix(in srgb, var(--ok, #67c23a) 12%, transparent);
  border-color: color-mix(in srgb, var(--ok, #67c23a) 40%, transparent);
}

.series-list,
.links,
.errors {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.series-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.series-main {
  display: grid;
  min-width: 0;
}

.cell-strong {
  font-weight: 600;
}

.cell-sub,
.muted {
  font-size: 12px;
  color: var(--muted);
}

.sub {
  margin: 16px 0 0;
  font-size: 13px;
  font-weight: 600;
}

.links a {
  color: var(--accent);
  font-size: 13px;
  text-decoration: underline;
}

.import-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.import-btn {
  padding: 4px 14px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.import-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.card-note,
.inline-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.inline-note.danger,
.errors {
  color: var(--danger);
}

.errors {
  font-size: 12px;
}
</style>
