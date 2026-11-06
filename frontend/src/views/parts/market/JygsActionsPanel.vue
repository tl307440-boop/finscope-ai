<script setup lang="ts">
/**
 * 韭研公社异动解析：按板块列出当日涨停 / 异动个股与人工整理的原因。
 * 数据需后端配置 JYGS_SESSION（扫码登录后的会话）；没配置或登录失效时如实说明，不造数。
 */
import { computed, ref } from "vue"
import { Notebook } from "@element-plus/icons-vue"

import { fetchJygsActions, fetchJygsStatus, type JygsSector } from "../../../api/aSharePanorama"
import { fmtNum, fmtPct, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import JygsLoginDialog from "../../../components/market/JygsLoginDialog.vue"
import MarketPanel from "../../../components/market/MarketPanel.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import { useSessionStore } from "../../../stores/auth"
import { useMarketLinkageStore } from "../../../stores/marketLinkage"

const linkageStore = useMarketLinkageStore()
const { data, state, error, reload } = usePanoramaResource(() => fetchJygsActions(), {
  pollMs: 120_000,
})

const status = usePanoramaResource(fetchJygsStatus, { pollMs: 300_000 })
const session = useSessionStore()
const isAdmin = computed(() => session.role === "admin")
const loginOpen = ref(false)
const needsLogin = computed(() => {
  const s = status.data.value
  return s !== null && s.state !== "ok" && s.state !== "unknown"
})

async function onLoggedIn(): Promise<void> {
  await Promise.all([reload(), status.reload()])
}

const opened = ref<Set<string>>(new Set())

function toggle(sector: JygsSector): void {
  const next = new Set(opened.value)
  if (next.has(sector.name)) next.delete(sector.name)
  else next.add(sector.name)
  opened.value = next
}

const panelSubtitle = computed(() => {
  const d = data.value
  if (d && d.sectors.length) {
    return `${d.tradeDate} · ${d.sectors.length} 个板块 · ${d.totalStocks} 只 · 点击板块展开，点击个股联动右侧详情`
  }
  return "人工整理的涨停 / 异动原因（交易日 12:00 后更新）"
})

const emptyMessage = computed(() => {
  const meta = data.value?.meta
  if (!meta) return ""
  const reasons = Object.values(meta.unavailable)
  if (reasons.length > 0) return reasons.join("；")
  const hint = meta.notes.find((n) => n.includes("JYGS_SESSION") || n.includes("暂无异动解析"))
  return hint ?? "韭研公社当日暂无异动解析。"
})
</script>

<template>
  <MarketPanel
    title="韭研公社 · 异动解析"
    :subtitle="panelSubtitle"
    info="韭研公社人工整理的涨停 / 异动原因，需要管理员扫码登录后才有数据"
    tone="violet"
    :icon="Notebook"
    :meta="data?.meta ?? null"
    :refresh-error="error"
  >
    <div v-if="needsLogin && status.data.value" class="jg-login" :class="`is-${status.data.value.state}`">
      <span class="jg-login-text">{{ status.data.value.message }}</span>
      <el-button v-if="isAdmin" size="small" type="primary" @click="loginOpen = true">
        扫码登录
      </el-button>
      <span v-else class="jg-login-hint">请联系管理员重新登录</span>
    </div>
    <JygsLoginDialog v-if="isAdmin" v-model="loginOpen" @success="onLoggedIn" />

    <StateBlock
      v-if="!data"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="异动解析加载失败"
      :rows="3"
      @retry="reload"
    />
    <template v-else>
      <StateBlock v-if="data.sectors.length === 0" state="empty" :message="emptyMessage" />
      <div v-else class="jg-list">
        <div v-for="sector in data.sectors" :key="sector.name" class="jg-sector" :class="{ open: opened.has(sector.name) }">
          <button type="button" class="jg-sector-head" @click="toggle(sector)">
            <b>{{ sector.name }}</b>
            <span class="jg-count">{{ sector.stocks.length }}</span>
            <span class="jg-reason" :title="sector.reason">{{ sector.reason || "—" }}</span>
            <span class="jg-arrow">{{ opened.has(sector.name) ? "收起" : "展开" }}</span>
          </button>
          <table v-if="opened.has(sector.name)" class="jg-table">
            <tbody>
              <tr
                v-for="s in sector.stocks"
                :key="s.code"
                class="clickable"
                @click="linkageStore.selectStock(s.code)"
              >
                <td class="strong">{{ s.name }} <span class="code">{{ s.code }}</span></td>
                <td class="board">{{ s.boardLabel || "—" }}</td>
                <td class="num">{{ fmtNum(s.price) }}</td>
                <td class="num" :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</td>
                <td class="num">{{ s.firstSealTime || "—" }}</td>
                <td class="explain" :title="s.explain">{{ s.explain || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </MarketPanel>
</template>

<style scoped>
.jg-login {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  font-size: 13px;
}

.jg-login.is-expiring {
  border-color: color-mix(in srgb, var(--warn) 55%, transparent);
  background: color-mix(in srgb, var(--warn) 8%, var(--panel-soft));
}

.jg-login.is-expired,
.jg-login.is-not_configured {
  border-color: color-mix(in srgb, var(--danger) 55%, transparent);
  background: color-mix(in srgb, var(--danger) 8%, var(--panel-soft));
}

.jg-login-text {
  flex: 1;
  color: var(--text);
}

.jg-login-hint {
  color: var(--muted);
}

.jg-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.jg-sector {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.jg-sector.open {
  border-color: color-mix(in srgb, var(--accent) 40%, var(--line));
}

.jg-sector-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: 0;
  background: var(--panel-soft);
  color: var(--text-strong);
  font-size: 14px;
  text-align: left;
  cursor: pointer;
}

.jg-sector-head:hover {
  background: var(--accent-soft);
}

.jg-count {
  min-width: 22px;
  padding: 0 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--danger) 14%, transparent);
  color: var(--danger);
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  text-align: center;
}

.jg-reason {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--muted);
}

.jg-arrow {
  font-size: 12px;
  color: var(--accent);
}

.jg-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.jg-table td {
  padding: 8px 12px;
  border-top: 1px solid var(--line-soft);
  vertical-align: top;
}

.jg-table .num {
  text-align: right;
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.jg-table .board {
  white-space: nowrap;
}

.jg-table .strong {
  white-space: nowrap;
  font-weight: 600;
  color: var(--text-strong);
}

.jg-table .code {
  font-family: var(--font-num);
  font-size: 12px;
  font-weight: 400;
  color: var(--faint);
}

.jg-table .clickable {
  cursor: pointer;
}

.jg-table .clickable:hover td {
  background: var(--accent-soft);
}

.jg-table .explain {
  max-width: 460px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
}
</style>