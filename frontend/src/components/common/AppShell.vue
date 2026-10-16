<script setup lang="ts">
/**
 * 应用外壳（规格书 §1.2，视觉对齐系统设计文件夹里的 10 张效果图）。
 *
 * 左侧 240px 固定导航，可折叠到 72px；顶部 64px 顶栏，包含品牌区、全局搜索、
 * 数据时间、数据状态、提醒铃铛、明暗主题与账号菜单。
 *
 * 导航用规格书的 11 个一级导航。效果图里是一列平铺条目，所以这里每个分组只把
 * 「主路由」渲染成一行；当当前路由命中该分组时，才在它下面缩进展开其余二级页面，
 * 这样静态看是 11 行（和效果图一致），又不会丢掉 §1.3 路由表里的任何一个页面。
 */
import { computed, onBeforeUnmount, onMounted, ref, type Component } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  ArrowDown,
  Bell,
  DArrowLeft,
  DArrowRight,
  DataAnalysis,
  DataBoard,
  Document,
  Histogram,
  HomeFilled,
  MagicStick,
  Monitor,
  Moon,
  Refresh,
  Setting,
  Star,
  Sunny,
  TrendCharts,
  Trophy,
} from "@element-plus/icons-vue"
import GlobalSearch from "./GlobalSearch.vue"
import { fetchSystemStatus, formatShanghai, reasonText, type SystemSnapshot } from "../../api/health"
import { applyTheme, readTheme, toggleTheme, type ThemeName } from "../../shell/theme"
import { logout, useSessionStore } from "../../stores/auth"

interface NavChild {
  index: string
  label: string
}

interface NavGroup {
  key: string
  label: string
  icon: Component
  /** 分组的第一行指向哪里 */
  primary: string
  adminOnly?: boolean
  /** 命中分组后缩进展开的其余页面 */
  children: NavChild[]
}

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

const theme = ref<ThemeName>("dark")
const collapsed = ref(false)
const snapshot = ref<SystemSnapshot | null>(null)
const statusError = ref("")

/** 11 个一级导航。规格书未列但已实现的能力挂在对应分组下，不删除。 */
const groups = computed<NavGroup[]>(() => {
  const items: NavGroup[] = [
    {
      key: "dashboard",
      label: "总览驾驶舱",
      icon: HomeFilled,
      primary: "/dashboard",
      children: [
        { index: "/research/board", label: "研究板" },
        { index: "/simulation/mvp", label: "研究推演" },
      ],
    },
    {
      key: "macro",
      label: "全球宏观",
      icon: TrendCharts,
      primary: "/macro/us",
      children: [
        { index: "/macro/china", label: "中国宏观" },
        { index: "/macro/liquidity", label: "货币流动性" },
        { index: "/macro/scenarios", label: "宏观情景分析" },
      ],
    },
    {
      key: "market",
      label: "市场行情",
      icon: Histogram,
      primary: "/market/indices",
      children: [
        { index: "/market/sectors", label: "板块排行" },
        { index: "/market/temperature", label: "市场温度" },
      ],
    },
    {
      key: "company",
      label: "公司研究",
      icon: DataAnalysis,
      primary: "/company/600519.SH",
      children: [
        { index: "/company/601318.SH", label: "中国平安 601318.SH" },
        { index: "/company/000858.SZ", label: "五粮液 000858.SZ" },
        { index: "/company/AAPL", label: "苹果 AAPL" },
      ],
    },
    {
      key: "industry",
      label: "产业研究",
      icon: DataBoard,
      primary: "/industry",
      children: [],
    },
    {
      key: "hotspots",
      label: "市场热点",
      icon: Trophy,
      primary: "/hotspots",
      children: [],
    },
    {
      key: "assistant",
      label: "AI投研助手",
      icon: MagicStick,
      primary: "/assistant",
      children: [],
    },
    {
      key: "reports",
      label: "研究报告",
      icon: Document,
      primary: "/reports",
      children: [],
    },
    {
      key: "watchlist",
      label: "自选关注",
      icon: Star,
      primary: "/watchlist",
      children: [],
    },
    {
      key: "data-center",
      label: "数据管理",
      icon: Monitor,
      primary: "/data-center",
      adminOnly: true,
      children: [],
    },
    {
      key: "settings",
      label: "系统设置",
      icon: Setting,
      primary: "/settings",
      children: [{ index: "/status", label: "运行状态" }],
    },
  ]
  return items.filter((item) => !item.adminOnly || session.role === "admin")
})

/** 当前路由属于哪一个分组（含该分组的全部二级页面）。 */
const activeKey = computed<string>(() => {
  const hit = groups.value.find(
    (group) => group.primary === route.path || group.children.some((child) => child.index === route.path),
  )
  return hit ? hit.key : ""
})

function isPrimary(group: NavGroup): boolean {
  return route.path === group.primary
}

function isChild(child: NavChild): boolean {
  return route.path === child.index
}

const accountLabel = computed(() => session.username ?? "未登录")
const accountRole = computed(() => {
  if (!session.accessToken) {
    return "未登录"
  }
  if (session.role === "admin") {
    return "系统管理员"
  }
  if (session.role === "researcher") {
    return "研究员"
  }
  return "只读用户"
})
const avatarText = computed(() => (session.username ? session.username.slice(0, 1).toUpperCase() : "访"))

const authenticated = computed(() => Boolean(session.accessToken))
const systemReady = computed(() => Boolean(snapshot.value?.ready))

/**
 * 全局底部状态条已经取消：效果图每页只有一条「数据截至 / 数据来源」栏，
 * 而每个页面自己的 SourceFooter 用的是该页 meta.as_of 与真实数据源，
 * 比全局栏更准。两栏堆在一起是重复的，所以只保留页面级那一栏。
 */
/**
 * 提醒项全部来自真实的就绪检查结果，没有后端通知接口就不编造条数。
 */
const alerts = computed<string[]>(() => {
  const list: string[] = []
  if (statusError.value) {
    list.push(`状态接口读取失败：${statusError.value}`)
    return list
  }
  if (!snapshot.value) {
    return list
  }
  if (snapshot.value.database.status !== "up") {
    list.push(`数据库不可用：${reasonText(snapshot.value.database.reason) ?? "未知原因"}`)
  }
  if (snapshot.value.worker.status !== "up") {
    list.push(`任务服务不可用：${reasonText(snapshot.value.worker.reason) ?? "未知原因"}`)
  }
  if (snapshot.value.notice) {
    list.push(snapshot.value.notice)
  }
  return list
})

function switchTheme(): void {
  theme.value = applyTheme(toggleTheme(theme.value))
}

function reloadPage(): void {
  window.location.reload()
}

function openAlert(command: string): void {
  void router.push(command)
}

function openAccount(command: string): void {
  if (command === "logout") {
    void logout().finally(() => router.push("/login"))
    return
  }
  void router.push(command)
}

const now = ref(new Date())
let clock: number | undefined

const clockDate = computed(() =>
  now.value.toLocaleDateString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }),
)
const clockTime = computed(() =>
  now.value.toLocaleTimeString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }),
)

onMounted(() => {
  theme.value = applyTheme(readTheme(window.localStorage.getItem("finscope-theme")))
  void loadStatus()
  clock = window.setInterval(() => {
    now.value = new Date()
  }, 1000)
})

onBeforeUnmount(() => {
  window.clearInterval(clock)
})

async function loadStatus(): Promise<void> {
  try {
    statusError.value = ""
    snapshot.value = await fetchSystemStatus()
  } catch (caught) {
    snapshot.value = null
    statusError.value = caught instanceof Error ? caught.message : "状态读取失败"
  }
}
</script>


<template>
  <div class="app-shell" :class="{ collapsed }" :data-layout="collapsed ? 'rail' : 'sidebar'">
    <a class="skip-link" href="#main">跳到主要内容</a>
    <aside class="side-nav" aria-label="主导航">
      <router-link class="brand" to="/dashboard" aria-label="FinScope AI 首页">
        <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
          <defs>
            <linearGradient id="fs-mark" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stop-color="#60a5fa" />
              <stop offset="1" stop-color="#22d3ee" />
            </linearGradient>
          </defs>
          <path d="M6 24 L13 15 L18 19 L26 8" fill="none" stroke="url(#fs-mark)" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" />
          <circle cx="26" cy="8" r="3" fill="#22d3ee" />
          <path d="M6 28 H26" stroke="rgb(96 165 250 / 45%)" stroke-width="2" stroke-linecap="round" />
        </svg>
        <span v-if="!collapsed" class="brand-text">
          <strong>FinScope <b>AI</b></strong>
          <em>智能投研 · 洞察未来</em>
        </span>
      </router-link>

      <nav class="rail" aria-label="一级导航">
        <template v-for="group in groups" :key="group.key">
          <router-link
            class="rail-row"
            :class="{ active: isPrimary(group), 'in-group': !isPrimary(group) && activeKey === group.key }"
            :to="group.primary"
            :title="group.label"
          >
            <el-icon class="rail-icon"><component :is="group.icon" /></el-icon>
            <span v-if="!collapsed" class="rail-label">{{ group.label }}</span>
          </router-link>
          <div v-if="!collapsed && activeKey === group.key && group.children.length" class="rail-children">
            <router-link
              v-for="child in group.children"
              :key="child.index"
              class="rail-sub"
              :class="{ active: isChild(child) }"
              :to="child.index"
            >
              {{ child.label }}
            </router-link>
          </div>
        </template>
      </nav>

      <div v-if="!collapsed" class="rail-foot">
        <strong>FinScope AI</strong>
        <span>让专业的投研更高效</span>
        <span class="version">v1.0.0 · <i class="rise">红涨</i> <i class="fall">绿跌</i></span>
      </div>
      <button
        class="rail-toggle"
        type="button"
        :aria-label="collapsed ? '展开导航' : '收起导航'"
        @click="collapsed = !collapsed"
      >
        <el-icon><component :is="collapsed ? DArrowRight : DArrowLeft" /></el-icon>
        <span v-if="!collapsed">收起导航</span>
      </button>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <GlobalSearch />
        <div class="nav-links">
          <span class="clock" :title="`系统数据时间 ${formatShanghai(snapshot?.asOf ?? '') || '—'}`">
            <span class="clock-date">{{ clockDate }}</span>
            <span class="clock-time">{{ clockTime }}</span>
          </span>
          <span class="topbar-chip" :class="systemReady ? 'up' : 'down'" :title="statusError || '数据库与任务服务的就绪检查'">
            <i class="dot" aria-hidden="true" />
            {{ systemReady ? "数据正常" : "部分异常" }}
          </span>

          <el-dropdown trigger="click" @command="openAlert">
            <button class="icon-button" type="button" aria-label="提醒">
              <el-icon><Bell /></el-icon>
              <span v-if="alerts.length" class="badge">{{ alerts.length }}</span>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="!alerts.length" disabled>暂无提醒</el-dropdown-item>
                <el-dropdown-item v-for="(item, index) in alerts" :key="index" command="/data-center">
                  {{ item }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <button class="icon-button" type="button" aria-label="刷新" title="刷新页面" @click="reloadPage">
            <el-icon><Refresh /></el-icon>
          </button>
          <button
            class="icon-button"
            type="button"
            :aria-label="theme === 'dark' ? '切换到浅色' : '切换到深色'"
            :title="theme === 'dark' ? '切换到浅色' : '切换到深色'"
            @click="switchTheme"
          >
            <el-icon><component :is="theme === 'dark' ? Sunny : Moon" /></el-icon>
          </button>

          <el-dropdown trigger="click" @command="openAccount">
            <button class="account" type="button">
              <span class="avatar" aria-hidden="true">{{ avatarText }}</span>
              <span class="who">
                <strong>{{ accountLabel }}</strong>
                <em>{{ accountRole }}</em>
              </span>
              <el-icon class="caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="/settings">系统设置</el-dropdown-item>
                <el-dropdown-item v-if="session.role === 'admin'" command="/data-center">
                  数据管理
                </el-dropdown-item>
                <el-dropdown-item v-if="authenticated" command="logout" divided>退出登录</el-dropdown-item>
                <el-dropdown-item v-else command="/login">登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>
      <main id="main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
/* ---- 品牌区 ---- */
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 8px 18px;
  color: inherit;
  text-decoration: none;
}

.mark {
  width: 34px;
  height: 34px;
  flex: none;
  padding: 4px;
  border-radius: 9px;
  background: linear-gradient(145deg, rgb(37 99 235 / 28%), rgb(34 211 238 / 10%));
  box-shadow: 0 0 0 1px rgb(96 165 250 / 30%) inset, 0 8px 20px -10px rgb(59 130 246 / 80%);
}

.brand-text {
  display: grid;
  min-width: 0;
}

.brand-text strong {
  font-size: 18px;
  line-height: 1.15;
  letter-spacing: 0.01em;
  white-space: nowrap;
  color: var(--text-strong);
}

.brand-text b {
  color: var(--cyan);
}

.brand-text em {
  font-style: normal;
  font-size: 11px;
  letter-spacing: 0.12em;
  color: var(--muted);
  white-space: nowrap;
}

/* ---- 一级导航 ---- */
.rail {
  flex: 1 1 auto;
  display: grid;
  gap: 3px;
  align-content: start;
  overflow-y: auto;
  overflow-x: hidden;
  margin: 0 -4px;
  padding: 0 4px;
}

.rail-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  height: 40px;
  padding: 0 12px;
  border-radius: 8px;
  color: var(--muted);
  text-decoration: none;
  font-size: 14px;
  white-space: nowrap;
  transition: background 140ms ease, color 140ms ease;
}

.rail-row:hover {
  background: rgb(96 140 220 / 8%);
  color: var(--text);
}

.rail-row.active {
  color: #fff;
  font-weight: 600;
  background: linear-gradient(90deg, #2563eb, rgb(37 99 235 / 55%));
  box-shadow: 0 0 0 1px rgb(96 165 250 / 40%) inset, 0 10px 24px -12px rgb(37 99 235 / 90%);
}

.rail-row.in-group {
  color: var(--text-strong);
  background: rgb(59 130 246 / 10%);
}

.rail-icon {
  font-size: 17px;
  flex: none;
}

.rail-children {
  display: grid;
  gap: 1px;
  margin: 2px 0 4px 20px;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}

.rail-sub {
  display: block;
  padding: 6px 10px;
  border-radius: 6px;
  color: var(--muted);
  text-decoration: none;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.rail-sub:hover {
  color: var(--text);
  background: rgb(96 140 220 / 8%);
}

.rail-sub.active {
  color: var(--accent);
  background: var(--accent-soft);
}

.rail-foot {
  display: grid;
  gap: 2px;
  margin-top: 12px;
  padding: 14px 10px 0;
  border-top: 1px solid var(--line-soft);
  font-size: 12px;
  color: var(--muted);
}

.rail-foot strong {
  color: var(--text);
  font-size: 13px;
}

.version {
  font-size: 11px;
  color: var(--faint);
}

.version i {
  font-style: normal;
}

.rail-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--faint);
  font-size: 12px;
  cursor: pointer;
}

.rail-toggle:hover {
  color: var(--text);
  background: rgb(96 140 220 / 8%);
}

.app-shell.collapsed .brand {
  justify-content: center;
  padding-left: 0;
  padding-right: 0;
}

.app-shell.collapsed .rail-row {
  justify-content: center;
  padding: 0;
}

.app-shell.collapsed .rail-toggle {
  align-self: center;
}

/* ---- 顶栏 ---- */
.clock {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  white-space: nowrap;
  color: var(--muted);
  font-size: 13px;
}

.clock-time {
  font-family: var(--font-num);
  font-size: 15px;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
  min-width: 64px;
}

.topbar-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}

.topbar-chip .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 8px currentColor;
}

.topbar-chip.up {
  color: var(--ok);
  background: rgb(52 211 153 / 10%);
}

.topbar-chip.down {
  color: var(--warn);
  background: rgb(251 191 36 / 10%);
}

.icon-button {
  position: relative;
  appearance: none;
  background: none;
  border: 0;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  color: var(--muted);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
}

.icon-button:hover {
  background: rgb(96 140 220 / 10%);
  color: var(--text-strong);
}

.badge {
  position: absolute;
  top: 2px;
  right: 1px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--rise);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  box-shadow: 0 0 0 2px var(--panel-deep);
}

.account {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  appearance: none;
  background: none;
  border: 0;
  border-left: 1px solid var(--line);
  margin-left: 4px;
  padding: 2px 0 2px 14px;
  color: var(--text);
  cursor: pointer;
}

.avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: none;
  border-radius: 50%;
  background: linear-gradient(135deg, #3b82f6, #22d3ee);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  box-shadow: 0 0 0 2px rgb(96 165 250 / 25%);
}

.who {
  display: grid;
  text-align: left;
  line-height: 1.25;
}

.who strong {
  font-size: 13px;
  white-space: nowrap;
}

.who em {
  font-style: normal;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.caret {
  color: var(--muted);
  font-size: 12px;
}

@media (max-width: 1279px) {
  .clock-date,
  .who {
    display: none;
  }
}
</style>
