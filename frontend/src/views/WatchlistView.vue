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
 */
import { computed, onMounted, ref } from "vue"
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
  actionError.value = ""
  moves.value = {}
  moveMeta.value = null

  if (!session.accessToken) {
    const restored = await restoreSession()
    if (!restored) {
      phase.value = "error"
      failure.value = "登录后可以使用自选"
      retryable.value = true
      return
    }
  }

  try {
    const result = await authed("/api/v1/watchlists", { method: "GET" })
    if (result.status === 401) {
      phase.value = "error"
      failure.value = "登录后可以使用自选"
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

function changeText(instrumentId: string): string {
  const move = moveOf(instrumentId)
  if (!move || move.changePercent === null) {
    return "—"
  }
  const value = move.changePercent
  const sign = value > 0 ? "+" : value < 0 ? "" : "±"
  return `${sign}${value.toFixed(2)}%`
}

/** 涨红跌绿（A 股习惯）：--rise 红、--fall 绿，同时带 +/- 符号。 */
function changeClass(instrumentId: string): string {
  const move = moveOf(instrumentId)
  if (!move || move.changePercent === null || move.changePercent === 0) {
    return "muted"
  }
  return move.changePercent > 0 ? "rise" : "fall"
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取自选</section>

    <section v-else-if="phase === 'error' && !signedIn" class="panel">
      <h1>自选研究</h1>
      <el-alert type="info" :closable="false" show-icon>
        <template #title>
          登录后可以使用自选，<a :href="loginHref">前往登录</a>。
        </template>
      </el-alert>
      <p class="muted">未登录时不会加载任何分组，也不显示任何行情数字。</p>
    </section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取自选</h1>
      <p>{{ failure }}</p>
      <button v-if="retryable" class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <header class="panel">
        <h1>自选研究</h1>
        <p class="muted">
          {{ groups.length }} 个分组 ·
          {{ groups.reduce((total, group) => total + group.items.length, 0) }} 只自选
        </p>
        <p v-if="moveMeta" class="muted">
          行情来源 {{ moveMeta.source || "未知" }}
          <template v-if="moveMeta.delay"> · 延迟 {{ moveMeta.delay }}</template>
          <template v-if="moveMeta.pitDate"> · 数据截止 {{ moveMeta.pitDate }}</template>
        </p>
        <form class="form" @submit.prevent="createGroup">
          <label>
            新建分组
            <input v-model="newGroupName" name="group-name" type="text" maxlength="40" placeholder="例如：白酒" />
          </label>
          <button class="retry" type="submit" :disabled="!canCreate">新建分组</button>
        </form>
        <p v-if="actionOk" role="status" class="muted">{{ actionOk }}</p>
        <p v-if="actionError" role="alert">{{ actionError }}</p>
      </header>

      <el-empty v-if="!groups.length" description="还没有自选分组，先新建一个分组再添加公司" />

      <section v-else class="panel">
        <h2>加入自选</h2>
        <el-select v-model="activeGroup" placeholder="选择分组">
          <el-option v-for="group in groups" :key="group.id" :label="group.name" :value="group.id" />
        </el-select>
        <form class="form" @submit.prevent="addFirstHit">
          <label>
            代码或名称
            <input
              v-model="query"
              name="instrument-query"
              type="text"
              placeholder="例如 600519.SH 或 贵州茅台"
              @input="scheduleSearch"
            />
          </label>
          <button class="retry" type="submit" :disabled="busy || !hits.length">加入第一个结果</button>
        </form>
        <p v-if="searching" class="muted">正在检索证券</p>
        <ul v-else-if="query.trim() && !hits.length" class="hit-list">
          <li class="muted">没有匹配的证券</li>
        </ul>
        <ul v-else-if="hits.length" class="hit-list">
          <li v-for="hit in hits" :key="hit.id">
            <a :href="routeForHit(hit)">{{ describeHit(hit) }}</a>
            <button class="retry" type="button" :disabled="busy" @click="addItem(hit)">加入自选</button>
          </li>
        </ul>
      </section>

      <section v-for="group in groups" :key="group.id" class="panel">
        <h2>{{ group.name }}</h2>
        <el-empty v-if="!group.items.length" description="这个分组还没有自选公司" />
        <el-table v-else :data="group.items" stripe>
          <el-table-column label="代码" width="140">
            <template #default="scope">
              <a :href="`/company/${encodeURIComponent(scope.row.displaySymbol)}`">
                {{ scope.row.displaySymbol }}
              </a>
            </template>
          </el-table-column>
          <el-table-column label="名称" min-width="140">
            <template #default="scope">{{ scope.row.nameCn || "—" }}</template>
          </el-table-column>
          <el-table-column label="最近收盘" width="120">
            <template #default="scope">
              <span v-if="moveOf(scope.row.instrumentId)?.close">
                {{ moveOf(scope.row.instrumentId)?.close }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="当日变化" width="120">
            <template #default="scope">
              <span :class="changeClass(scope.row.instrumentId)">
                {{ changeText(scope.row.instrumentId) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="交易日" width="120">
            <template #default="scope">
              {{ moveOf(scope.row.instrumentId)?.tradeDate || "—" }}
            </template>
          </el-table-column>
          <el-table-column label="行情状态" min-width="150">
            <template #default="scope">
              <span v-if="moveOf(scope.row.instrumentId)?.unavailable" class="muted">
                不可用：{{ moveOf(scope.row.instrumentId)?.unavailable }}
              </span>
              <span v-else-if="moveOf(scope.row.instrumentId)" class="muted">已加载</span>
              <span v-else class="muted">未加载</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="scope">
              <button class="retry" type="button" :disabled="busy" @click="removeItem(group, scope.row)">
                移出
              </button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="panel">
        <h2>关注事件</h2>
        <el-alert type="info" :closable="false" show-icon>
          <template #title>后端还没有自选维度的关注事件接口，这里不生成事件条目。</template>
        </el-alert>
        <p class="muted">
          可核对的只有上表的真实日线收盘与当日变化。事件线索请到公司页的财报资料与产业页的已入库事实上查。
        </p>
        <p class="muted">页面不生成任何时间戳，行情数据截止日以上表「交易日」与页首的数据截止为准。</p>
      </section>
    </template>
  </main>
</template>

<style scoped>
h2 {
  margin: 0 0 12px;
  font-size: 15px;
}

.el-alert {
  margin-bottom: 12px;
}

.el-select {
  min-width: 220px;
  margin-bottom: 12px;
}

.el-table {
  margin-top: 8px;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}

.form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin-top: 8px;
}

.form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

.form input {
  min-width: 220px;
}

.hit-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.hit-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
}

.rise {
  color: var(--rise);
}

.fall {
  color: var(--fall);
}
</style>
