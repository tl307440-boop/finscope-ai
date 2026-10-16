<script setup lang="ts">
/**
 * 自选研究（/watchlist，需登录；路由里已带 meta.requiresAuth）。
 *
 *  - GET    /api/v1/watchlists                                     我的自选
 *  - POST   /api/v1/watchlists                                    新建分组（GroupCreate）
 *  - POST   /api/v1/watchlists/{group_id}/items                   加入自选（ItemCreate）
 *  - DELETE /api/v1/watchlists/{group_id}/items/{instrument_id}   移出自选
 *  - GET    /api/v1/instruments/search                             选证券
 *  - GET    /api/v1/market/bars/{instrument_id}?limit=                指标变化（收盘价 + simple_return）
 *
 * 未登录时不报错：显示「登录后可以使用自选」并给出带 redirect 的登录链接。
 * 指标变化只在真实取到 K 线时显示；取不到就标注不可用，不做任何推算或占位数字。
 *
 * 布局：页首 PageHeader + 四张概览小卡 + 「加入自选」+ 每个分组一张表 + 「关注事件」说明。
 */
import { computed, onMounted, ref } from "vue"
import { Bell, Clock, Coin, Plus, Search, Star, TrendCharts } from "@element-plus/icons-vue"
import DeltaValue from "../components/ui/DeltaValue.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, apiUrl, authorizedFetch, readJson } from "../api/client"
import { describeHit, routeForHit, searchInstruments, type InstrumentHit } from "../api/instruments"
import { asString, dataOf, listOf } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface WatchItem {
  instrumentId: string
  displaySymbol: string
  nameCn: string
}

interface WatchGroup {
  id: string
  name: string
  items: WatchItem[]
}

interface MoveState {
  symbol: string
  close: string
  changePercent: number | null
  tradeDate: string
  unavailable: string
}

interface MoveMeta {
  source: string
  delay: string
  pitDate: string
}

type Phase = "loading" | "ready" | "error"

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
/** 未登录（本地无 token 或接口回 401）时走「登录后可以使用自选」分支，而不是报错。 */
const needsLogin = ref(false)
const groups = ref<WatchGroup[]>([])
const moves = ref<Record<string, MoveState>>({})
const moveMeta = ref<MoveMeta | null>(null)
const newGroupName = ref("")
const activeGroup = ref("")
const query = ref("")
const hits = ref<InstrumentHit[]>([])
const searched = ref(false)
const searching = ref(false)
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)
let searchTimer: ReturnType<typeof setTimeout> | null = null

const signedIn = computed<boolean>(() => Boolean(session.accessToken))
const loginHref = computed<string>(() => `/login?redirect=${encodeURIComponent("/watchlist")}`)
const canCreate = computed<boolean>(() => newGroupName.value.trim().length > 0 && !busy.value)

/* ---- 概览：只统计真实返回的结构，缺行情就计入「待入库」，不推算 ---- */
const itemCount = computed<number>(() =>
  groups.value.reduce((total, group) => total + group.items.length, 0),
)

const trackedIds = computed<string[]>(() =>
  Array.from(new Set(groups.value.flatMap((group) => group.items.map((item) => item.instrumentId)))),
)

const quotedCount = computed<number>(() =>
  trackedIds.value.filter((id) => {
    const move = moves.value[id]
    return Boolean(move && !move.unavailable && move.close)
  }).length,
)

const missingCount = computed<number>(() => trackedIds.value.length - quotedCount.value)

const latestTradeDate = computed<string | null>(() => {
  const dates = Object.values(moves.value)
    .map((move) => move.tradeDate)
    .filter((date): date is string => Boolean(date))
  if (!dates.length) {
    return null
  }
  return dates.sort()[dates.length - 1]
})

const overviewCaption = computed<string>(() => {
  if (!groups.value.length) {
    return "还没有分组，先新建一个"
  }
  if (!trackedIds.value.length) {
    return "分组里还没有标的"
  }
  return groups.value.map((group) => group.name).join("、")
})

const missingCaption = computed<string>(() => {
  if (!trackedIds.value.length) {
    return "加入自选后显示"
  }
  return missingCount.value ? `${missingCount.value} 只暂无日线` : "全部都有已入库日线"
})

const SOURCE_TEXT: Record<string, string> = {
  akshare: "AKShare",
  tushare: "Tushare",
}

const DELAY_TEXT: Record<string, string> = {
  eod: "收盘后更新",
  realtime: "实时",
}

const sourceCaption = computed<string>(() => {
  if (!moveMeta.value || !quotedCount.value) {
    return trackedIds.value.length ? "自选标的暂无已入库日线" : "加入自选后显示"
  }
  const parts: string[] = []
  if (moveMeta.value.source) {
    parts.push(`来源 ${SOURCE_TEXT[moveMeta.value.source] ?? moveMeta.value.source}`)
  }
  if (moveMeta.value.delay) {
    parts.push(DELAY_TEXT[moveMeta.value.delay] ?? `延迟 ${moveMeta.value.delay}`)
  }
  return parts.join(" · ") || "已入库日线"
})

const uniqueCaption = computed<string>(() => {
  if (!itemCount.value) {
    return "还没有加入任何标的"
  }
  return itemCount.value === trackedIds.value.length
    ? "各分组合计"
    : `去重后 ${trackedIds.value.length} 只`
})

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "请求未被接受"
}

async function authed(path: string, init: RequestInit): Promise<{ ok: boolean; status: number; body: unknown }> {
  const response = await authorizedFetch(path, init, session.accessToken, refreshToken)
  const body = await readJson(response)
  return { ok: response.ok, status: response.status, body }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  needsLogin.value = false
  actionError.value = ""
  moves.value = {}
  moveMeta.value = null

  if (!session.accessToken) {
    const restored = await restoreSession()
    if (!restored) {
      phase.value = "error"
      failure.value = "登录后可以使用自选"
      needsLogin.value = true
      retryable.value = true
      return
    }
  }

  try {
    const result = await authed("/api/v1/watchlists", { method: "GET" })
    if (result.status === 401) {
      phase.value = "error"
      failure.value = "登录后可以使用自选"
      needsLogin.value = true
      return
    }
    if (!result.ok) {
      phase.value = "error"
      retryable.value = false
      failure.value = serverMessage(result.body)
      return
    }
    groups.value = listOf(dataOf(result.body)?.groups).flatMap(parseGroup)
    if (!groups.value.some((group) => group.id === activeGroup.value)) {
      activeGroup.value = groups.value[0]?.id ?? ""
    }
    phase.value = "ready"
    await loadMoves()
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

/** 逐档取最近两根日线，用后端给的 simple_return 展示当日变化；失败即标注不可用。 */
async function loadMoves(): Promise<void> {
  const symbols = Array.from(new Set(groups.value.flatMap((group) => group.items.map((item) => item.instrumentId))))
  const results = await Promise.all(
    symbols.map(async (instrumentId) => {
      try {
        const response = await fetch(apiUrl(`/api/v1/market/bars/${encodeURIComponent(instrumentId)}?limit=2`))
        const body = await readJson(response)
        if (!response.ok) {
          return [instrumentId, unavailableMove(serverMessage(body))] as const
        }
        const data = dataOf(body)
        const bars = listOf(data?.bars).flatMap((value) => {
          const row = asRecord(value)
          const date = asString(row?.trade_date)
          if (!date) {
            return []
          }
          return [
            {
              tradeDate: date,
              close: asString(row?.close) ?? "",
              simpleReturn: asString(row?.simple_return),
            },
          ]
        })
        if (moveMeta.value === null) {
          moveMeta.value = {
            source: asString(data?.source) ?? "",
            delay: asString(data?.delay) ?? "",
            pitDate: asString(data?.pit_date) ?? "",
          }
        }
        if (!bars.length) {
          return [instrumentId, unavailableMove("没有已入库日线")] as const
        }
        const latest = bars[bars.length - 1]
        const parsed = latest.simpleReturn === null ? null : Number.parseFloat(latest.simpleReturn)
        return [
          instrumentId,
          {
            symbol: asString(data?.display_symbol) ?? "",
            close: latest.close,
            changePercent: parsed !== null && Number.isFinite(parsed) ? parsed * 100 : null,
            tradeDate: latest.tradeDate,
            unavailable: "",
          },
        ] as const
      } catch {
        return [instrumentId, unavailableMove("无法读取行情")] as const
      }
    }),
  )
  const next: Record<string, MoveState> = {}
  for (const [instrumentId, state] of results) {
    next[instrumentId] = state
  }
  moves.value = next
}

function unavailableMove(reason: string): MoveState {
  return { symbol: "", close: "", changePercent: null, tradeDate: "", unavailable: reason }
}

async function createGroup(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  const name = newGroupName.value.trim()
  if (!name) {
    actionError.value = "分组名不能为空"
    return
  }
  busy.value = true
  try {
    const result = await authed("/api/v1/watchlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    })
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    newGroupName.value = ""
    actionOk.value = `分组「${asString(dataOf(result.body)?.name) ?? name}」已建立`
    await load()
  } catch (caught) {
    actionError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    busy.value = false
  }
}

function scheduleSearch(): void {
  hits.value = []
  searched.value = false
  actionError.value = ""
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  const keyword = query.value.trim()
  if (!keyword) {
    searching.value = false
    return
  }
  searching.value = true
  searchTimer = setTimeout(() => {
    searchTimer = null
    void runSearch()
  }, 350)
}

/** searchInstruments() 内部会 abort 上一次请求，重叠调用只会保留最后一次结果。 */
async function runSearch(): Promise<void> {
  const keyword = query.value.trim()
  if (!keyword) {
    return
  }
  searching.value = true
  try {
    const result = await searchInstruments(keyword, undefined, 8)
    hits.value = result.results
    if (!result.results.length) {
      actionError.value = result.emptyMessage ?? "没有匹配的证券"
    }
  } catch (caught) {
    hits.value = []
    actionError.value = caught instanceof ApiError ? caught.message : "证券检索失败"
  } finally {
    searched.value = true
    searching.value = false
  }
}

async function addItem(hit: InstrumentHit): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!activeGroup.value) {
    actionError.value = "请先选择或新建一个分组"
    return
  }
  if (groups.value.some((group) => group.id === activeGroup.value && group.items.some((item) => item.instrumentId === hit.id))) {
    actionError.value = `${hit.displaySymbol} 已经在该分组里`
    return
  }
  busy.value = true
  try {
    const result = await authed(`/api/v1/watchlists/${encodeURIComponent(activeGroup.value)}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument_id: hit.id }),
    })
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    actionOk.value = `${hit.displaySymbol} 已加入自选`
    query.value = ""
    hits.value = []
    searched.value = false
    await load()
  } catch (caught) {
    actionError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    busy.value = false
  }
}

async function addFirstHit(): Promise<void> {
  if (searchTimer !== null) {
    clearTimeout(searchTimer)
    searchTimer = null
  }
  if (!hits.value.length) {
    await runSearch()
  }
  const hit = hits.value[0]
  if (!hit) {
    actionError.value = actionError.value || "先检索出一个证券再加入自选"
    return
  }
  await addItem(hit)
}

async function removeItem(group: WatchGroup, item: WatchItem): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  busy.value = true
  try {
    const result = await authed(
      `/api/v1/watchlists/${encodeURIComponent(group.id)}/items/${encodeURIComponent(item.instrumentId)}`,
      { method: "DELETE" },
    )
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    actionOk.value = `${item.displaySymbol} 已移出「${group.name}」`
    await load()
  } catch (caught) {
    actionError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    busy.value = false
  }
}

function parseGroup(value: unknown): WatchGroup[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      name: asString(row?.name) ?? "未命名分组",
      items: listOf(row?.items).flatMap(parseItem),
    },
  ]
}

function parseItem(value: unknown): WatchItem[] {
  const row = asRecord(value)
  const instrumentId = asString(row?.instrument_id)
  if (!instrumentId) {
    return []
  }
  return [
    {
      instrumentId,
      displaySymbol: asString(row?.display_symbol) ?? instrumentId,
      nameCn: asString(row?.name_cn) ?? "",
    },
  ]
}

function moveOf(instrumentId: string): MoveState | null {
  return moves.value[instrumentId] ?? null
}

/** 只保留两位小数：后端 simple_return 是长浮点，直接渲染会带出 17 位尾数。 */
function changePercentOf(instrumentId: string): number | null {
  const raw = moveOf(instrumentId)?.changePercent
  if (raw === null || raw === undefined || !Number.isFinite(raw)) {
    return null
  }
  return Math.round(raw * 100) / 100
}

function statusText(instrumentId: string): string {
  const move = moveOf(instrumentId)
  if (!move) {
    return "未加载"
  }
  if (move.unavailable) {
    return move.unavailable
  }
  return "行情已入库"
}

function statusTone(instrumentId: string): "ok" | "warn" | "muted" {
  const move = moveOf(instrumentId)
  if (!move) {
    return "muted"
  }
  return move.unavailable ? "warn" : "ok"
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      :title="signedIn ? '自选研究' : '我的收藏'"
      subtitle="把关注的标的收进分组，用已入库日线跟踪当日变化"
      :dense="phase !== 'ready'"
    />

    <div v-if="phase === 'loading'" class="stack">
      <PanelCard title="我的自选">
        <StateBlock state="loading" :rows="4" />
      </PanelCard>
    </div>

    <div v-else-if="needsLogin" class="stack">
      <PanelCard title="我的自选">
        <StateBlock state="denied" message="自选分组跟随账号保存，登录后即可查看和管理">
          <a class="login-line" :href="loginHref">前往登录</a>
        </StateBlock>
      </PanelCard>
    </div>

    <div v-else-if="phase === 'error'" class="stack">
      <PanelCard title="我的自选">
        <StateBlock
          state="error"
          :message="failure"
          @retry="load"
        />
        <p v-if="!retryable" class="card-note">分组可能已被删除，刷新页面或重新登录后再试。</p>
      </PanelCard>
    </div>

    <div v-else class="stack">
      <div class="grid-4">
        <StatCard
          title="自选分组"
          :value="groups.length"
          unit="个"
          :caption="overviewCaption"
          info="你建立的自选分组数量"
          tone="accent"
        >
          <template #icon><el-icon><Star /></el-icon></template>
        </StatCard>
        <StatCard
          title="自选标的"
          :value="itemCount"
          unit="只"
          :caption="uniqueCaption"
          info="各分组标的数合计；同一标的出现在多个分组时会重复计数"
          tone="cyan"
        >
          <template #icon><el-icon><TrendCharts /></el-icon></template>
        </StatCard>
        <StatCard
          title="已入库行情"
          :value="quotedCount"
          unit="只"
          :caption="missingCaption"
          info="有已入库日线、可以显示收盘价的自选标的数量"
          tone="ok"
        >
          <template #icon><el-icon><Coin /></el-icon></template>
        </StatCard>
        <StatCard
          title="行情数据截止"
          :value="latestTradeDate"
          :caption="sourceCaption"
          info="自选标的已入库日线里最新的交易日，不是当前时间"
          tone="violet"
        >
          <template #icon><el-icon><Clock /></el-icon></template>
        </StatCard>
      </div>

      <div class="grid-2">
        <PanelCard title="新建分组" info="按主题或行业建分组，例如「白酒」「银行」；名称最长 40 个字">
          <template #icon><el-icon><Plus /></el-icon></template>
          <form class="inline-form" @submit.prevent="createGroup">
            <el-input
              v-model="newGroupName"
              name="group-name"
              type="text"
              maxlength="40"
              placeholder="分组名称，例如：白酒"
              aria-label="分组名称"
              clearable
            />
            <el-button type="primary" native-type="submit" :disabled="!canCreate" :loading="busy">
              新建分组
            </el-button>
          </form>
        </PanelCard>

        <PanelCard title="加入自选" info="输入代码或名称，检索已收录的证券后加入所选分组">
          <template #icon><el-icon><Search /></el-icon></template>
          <p v-if="!groups.length" class="inline-note first">先在左侧新建一个分组，再把关注的标的加进来。</p>
          <template v-else>
            <div class="search-row">
              <el-select v-model="activeGroup" placeholder="选择分组" aria-label="选择分组" style="width: 160px">
                <el-option v-for="group in groups" :key="group.id" :label="group.name" :value="group.id" />
              </el-select>
              <el-input
                v-model="query"
                name="instrument-query"
                type="text"
                placeholder="例如 600519.SH 或 贵州茅台"
                aria-label="证券代码或名称"
                clearable
                @input="scheduleSearch"
              />
              <el-button :disabled="busy || !hits.length" :loading="busy" @click="addFirstHit">
                加入第一个结果
              </el-button>
            </div>

            <p v-if="searching" class="inline-note">正在检索证券…</p>
            <ul v-else-if="hits.length" class="hit-list">
              <li v-for="hit in hits" :key="hit.id">
                <router-link :to="routeForHit(hit)">{{ describeHit(hit) }}</router-link>
                <el-button size="small" :disabled="busy" @click="addItem(hit)">加入自选</el-button>
              </li>
            </ul>
            <p v-else-if="query.trim() && searched" class="inline-note">没有匹配的证券</p>
          </template>
        </PanelCard>
      </div>

      <p v-if="actionOk" class="action-note" role="status">{{ actionOk }}</p>
      <p v-if="actionError" class="action-note danger" role="alert">{{ actionError }}</p>

      <PanelCard v-if="!groups.length" title="我的自选">
        <template #icon><el-icon><Star /></el-icon></template>
        <StateBlock state="empty" message="还没有自选分组。新建分组并加入标的后，这里会列出最近收盘与当日涨跌。" />
      </PanelCard>

      <PanelCard
        v-for="group in groups"
        :key="group.id"
        :title="group.name"
        flush
      >
        <template #icon><el-icon><Star /></el-icon></template>
        <template #actions>
          <span class="group-count">{{ group.items.length }} 只</span>
        </template>

        <StateBlock v-if="!group.items.length" state="empty" message="这个分组还没有标的，可在上方「加入自选」里添加" />
        <el-table v-else :data="group.items" size="small" style="width: 100%">
          <el-table-column label="代码" width="140">
            <template #default="scope">
              <router-link
                class="cell-link"
                :to="`/company/${encodeURIComponent(scope.row.displaySymbol)}`"
              >
                {{ scope.row.displaySymbol }}
              </router-link>
            </template>
          </el-table-column>
          <el-table-column label="名称" min-width="120">
            <template #default="scope">{{ scope.row.nameCn || "—" }}</template>
          </el-table-column>
          <el-table-column label="最近收盘" width="120" align="right">
            <template #default="scope">
              <DeltaValue
                v-if="moveOf(scope.row.instrumentId)?.close"
                :value="moveOf(scope.row.instrumentId)?.close ?? null"
                :colored="false"
              />
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="当日涨跌" width="120" align="right">
            <template #default="scope">
              <DeltaValue
                :value="changePercentOf(scope.row.instrumentId)"
                suffix="%"
                size="sm"
                :signed="true"
              />
            </template>
          </el-table-column>
          <el-table-column label="交易日" width="120">
            <template #default="scope">
              <span class="muted tabular">{{ moveOf(scope.row.instrumentId)?.tradeDate || "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="行情状态" min-width="160">
            <template #default="scope">
              <StatusPill
                :text="statusText(scope.row.instrumentId)"
                :tone="statusTone(scope.row.instrumentId)"
                dot
              />
            </template>
          </el-table-column>
          <el-table-column label="操作" width="96" fixed="right">
            <template #default="scope">
              <el-button size="small" :disabled="busy" @click="removeItem(group, scope.row)">移出</el-button>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <PanelCard title="关注事件" more-text="市场热点" more-to="/hotspots">
        <template #icon><el-icon><Bell /></el-icon></template>
        <p class="inline-note first">
          暂不按自选标的汇总公告事件。近期公告可在「市场热点」查看，单家公司的财报与公告在公司页查看。
        </p>
      </PanelCard>
    </div>
  </main>
</template>

<style scoped>
.login-line {
  flex: none;
  color: var(--accent);
  font-size: 13px;
}

.group-count {
  font-size: 13px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.inline-form,
.search-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.inline-form :deep(.el-input),
.search-row :deep(.el-input) {
  flex: 1 1 auto;
  min-width: 0;
}

.search-row :deep(.el-select) {
  flex: none;
}

.hit-list {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: grid;
}

.hit-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 2px;
  border-top: 1px solid var(--line-soft);
  font-size: 13px;
}

.hit-list li:first-child {
  border-top: 0;
}

.cell-link {
  color: var(--accent);
}

.inline-note {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.inline-note.first {
  margin-top: 0;
}

.inline-note.danger,
.action-note.danger {
  color: var(--danger);
}

.action-note {
  margin: 0;
  font-size: 13px;
  color: var(--ok);
}

.card-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.6;
}

.muted {
  color: var(--muted);
}

@media (max-width: 767px) {
  .search-row {
    flex-wrap: wrap;
  }
}
</style>
