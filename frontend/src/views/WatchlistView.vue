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
import { Bell, Plus, Search, Star, TrendCharts } from "@element-plus/icons-vue"
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

const latestTradeDate = computed<string>(() => {
  const dates = Object.values(moves.value)
    .map((move) => move.tradeDate)
    .filter((date): date is string => Boolean(date))
  if (!dates.length) {
    return "—"
  }
  return dates.sort()[dates.length - 1]
})

const overviewCaption = computed<string>(() => {
  if (!groups.value.length) {
    return "还没有分组，先新建一个"
  }
  if (!trackedIds.value.length) {
    return `${groups.value.length} 个分组里还没有自选`
  }
  return `共 ${groups.value.length} 个分组`
})

const missingCaption = computed<string>(() => {
  if (!trackedIds.value.length) {
    return "暂无自选，无可统计的行情"
  }
  return missingCount.value ? "已入库日线还没覆盖到" : "全部都有已入库日线"
})

const sourceCaption = computed<string>(() => {
  if (!moveMeta.value) {
    return "还没有取到任何 K 线"
  }
  const parts = [`来源 ${moveMeta.value.source || "未知"}`]
  if (moveMeta.value.delay) {
    parts.push(`延迟 ${moveMeta.value.delay}`)
  }
  if (moveMeta.value.pitDate) {
    parts.push(`数据截止 ${moveMeta.value.pitDate}`)
  }
  return parts.join(" · ")
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
    return `不可用：${move.unavailable}`
  }
  return "已加载"
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
      <PanelCard title="我的自选" info="未登录时不发任何分组请求，也不显示任何行情数字">
        <StateBlock state="denied" message="登录后可以使用自选">
          <p class="login-line">
            登录后可以使用自选，<a :href="loginHref">前往登录</a>。
          </p>
        </StateBlock>
        <p class="card-note">这里刻意不渲染任何占位行情：登录前接口不会返回分组与日线。</p>
      </PanelCard>
    </div>

    <div v-else-if="phase === 'error'" class="stack">
      <PanelCard title="我的自选">
        <StateBlock
          state="error"
          :message="failure"
          @retry="load"
        />
        <p v-if="!retryable" class="card-note">
          这次失败来自业务接口，可能是该分组已不存在；刷新页面或重新登录后再试。
        </p>
      </PanelCard>
    </div>

    <div v-else class="stack">
      <div class="grid-4">
        <StatCard
          title="自选分组"
          :value="groups.length"
          unit="个"
          :caption="overviewCaption"
          info="来自 /api/v1/watchlists 返回的 groups"
          more-text=""
        >
          <template #icon><el-icon><Star /></el-icon></template>
        </StatCard>
        <StatCard
          title="自选标的"
          :value="itemCount"
          unit="只"
          caption="去重后的行情请求按标的逐档发起"
          info="groups[].items 的条数合计，可能包含跨分组重复的标的"
          more-text=""
        >
          <template #icon><el-icon><TrendCharts /></el-icon></template>
        </StatCard>
        <StatCard
          title="已入库行情"
          :value="quotedCount"
          unit="只"
          :caption="`${missingCaption}（${missingCount} 只）`"
          info="GET /api/v1/market/bars/{instrument_id}?limit=2 真实返回 K 线的标的数量"
          more-text=""
        >
          <template #icon><el-icon><Coin /></el-icon></template>
        </StatCard>
        <StatCard
          title="行情数据截止"
          :value="latestTradeDate"
          :caption="sourceCaption"
          info="所有已取到 K 线里最新的 trade_date，不是当前系统时间"
          more-text=""
        >
          <template #icon><el-icon><Clock /></el-icon></template>
        </StatCard>
      </div>

      <PanelCard title="新建分组" info="POST /api/v1/watchlists">
        <template #icon><el-icon><Plus /></el-icon></template>
        <form class="inline-form" @submit.prevent="createGroup">
          <el-input
            v-model="newGroupName"
            name="group-name"
            type="text"
            maxlength="40"
            placeholder="例如：白酒"
            clearable
          />
          <el-button type="primary" native-type="submit" :disabled="!canCreate" :loading="busy">
            新建分组
          </el-button>
        </form>
        <p v-if="actionOk" class="inline-note" role="status">{{ actionOk }}</p>
        <p v-if="actionError" class="inline-note danger" role="alert">{{ actionError }}</p>
      </PanelCard>

      <PanelCard
        title="加入自选"
        info="POST /api/v1/watchlists/{group_id}/items，标的先经 /api/v1/instruments/search 换取 UUID"
      >
        <template #icon><el-icon><Search /></el-icon></template>
        <StateBlock
          v-if="!groups.length"
          state="empty"
          message="还没有自选分组，先在上面的卡片里新建一个分组再添加公司"
        />
        <template v-else>
          <div class="search-row">
            <el-select v-model="activeGroup" placeholder="选择分组" style="width: 220px">
              <el-option v-for="group in groups" :key="group.id" :label="group.name" :value="group.id" />
            </el-select>
            <el-input
              v-model="query"
              name="instrument-query"
              type="text"
              placeholder="例如 600519.SH 或 贵州茅台"
              clearable
              style="max-width: 280px"
              @input="scheduleSearch"
            />
            <el-button :disabled="busy || !hits.length" :loading="busy" @click="addFirstHit">
              加入第一个结果
            </el-button>
          </div>

          <p v-if="searching" class="inline-note">正在检索证券</p>
          <ul v-else-if="hits.length" class="hit-list">
            <li v-for="hit in hits" :key="hit.id">
              <router-link :to="routeForHit(hit)">{{ describeHit(hit) }}</router-link>
              <el-button size="small" :disabled="busy" @click="addItem(hit)">加入自选</el-button>
            </li>
          </ul>
          <p v-else-if="query.trim() && searched" class="inline-note">没有匹配的证券</p>
        </template>
      </PanelCard>

      <PanelCard
        v-for="group in groups"
        :key="group.id"
        :title="group.name"
        :info="`GET /api/v1/watchlists 分组 ${group.name} 的 items`"
      >
        <template #icon><el-icon><Star /></el-icon></template>
        <template #actions>
          <span class="group-count">{{ group.items.length }} 只 · 分组 ID {{ group.id.slice(0, 8) }}</span>
        </template>

        <StateBlock v-if="!group.items.length" state="empty" message="这个分组还没有自选公司" />
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
          <el-table-column label="当日变化" width="120" align="right">
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
              <span class="muted">{{ moveOf(scope.row.instrumentId)?.tradeDate || "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="行情状态" min-width="180">
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

      <PanelCard title="关注事件" info="后端还没有自选维度的关注事件接口，这里不生成事件条目">
        <template #icon><el-icon><Bell /></el-icon></template>
        <StateBlock state="empty" message="后端还没有自选维度的关注事件接口，这里不生成事件条目。" />
        <p class="card-note">
          可核对的只有上表的真实日线收盘与当日变化。事件线索请到公司页的财报资料与产业页的已入库事实上查。
        </p>
        <p class="card-note">
          页面不生成任何时间戳，行情数据截止日以上表「交易日」与「行情数据截止」卡为准。
        </p>
      </PanelCard>
    </div>
  </main>
</template>

<style scoped>
.stack {
  display: grid;
  gap: var(--gap);
}

.login-line {
  margin: 0;
}

.login-line a {
  color: var(--accent);
}

.group-count {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.inline-form,
.search-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.inline-form :deep(.el-input),
.search-row :deep(.el-input) {
  max-width: 240px;
}

.hit-list {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.hit-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--panel-soft);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
}

.cell-link {
  color: var(--accent);
}

.inline-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.inline-note.danger {
  color: var(--danger);
}

.card-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.muted {
  color: var(--muted);
}
</style>
