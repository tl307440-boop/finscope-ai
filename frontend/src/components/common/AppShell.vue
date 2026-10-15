<script setup lang="ts">
/**
 * 应用外壳（规格书 §1.2）。
 *
 * 左侧 232px 固定导航，可折叠到 72px；顶部 56px 顶栏，包含全局搜索、
 * 刷新、数据时间、任务服务提示、明暗主题与账号菜单。
 * 导航按规格书的 11 个一级导航分组，二级功能对应到 §1.3 的路由表。
 */
import { computed, onMounted, ref, type Component } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  ArrowDown,
  DataAnalysis,
  DataBoard,
  Document,
  Histogram,
  HomeFilled,
  MagicStick,
  Monitor,
  Refresh,
  Setting,
  Star,
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
  adminOnly?: boolean
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
      children: [
        { index: "/dashboard", label: "研究驾驶舱" },
        { index: "/research/board", label: "研究板" },
        { index: "/simulation/mvp", label: "研究推演" },
      ],
    },
    {
      key: "macro",
      label: "全球宏观",
      icon: TrendCharts,
      children: [
        { index: "/macro/us", label: "美国宏观" },
        { index: "/macro/china", label: "中国宏观" },
        { index: "/macro/liquidity", label: "货币流动性" },
        { index: "/macro/scenarios", label: "宏观情景分析" },
      ],
    },
    {
      key: "market",
      label: "市场行情",
      icon: Histogram,
      children: [
        { index: "/market/indices", label: "指数行情" },
        { index: "/market/sectors", label: "板块排行" },
        { index: "/market/temperature", label: "市场温度" },
      ],
    },
    {
      key: "company",
      label: "公司研究",
      icon: DataAnalysis,
      children: [
        { index: "/company/600519.SH", label: "贵州茅台 600519.SH" },
        { index: "/company/601318.SH", label: "中国平安 601318.SH" },
        { index: "/company/000858.SZ", label: "五粮液 000858.SZ" },
        { index: "/company/AAPL", label: "苹果 AAPL" },
      ],
    },
    {
      key: "industry",
      label: "产业研究",
      icon: DataBoard,
      children: [{ index: "/industry", label: "产业链与假设" }],
    },
    {
      key: "hotspots",
      label: "市场热点",
      icon: Trophy,
      children: [{ index: "/hotspots", label: "热点与事件" }],
    },
    {
      key: "assistant",
      label: "AI投研助手",
      icon: MagicStick,
      children: [{ index: "/assistant", label: "智能问答与引用" }],
    },
    {
      key: "reports",
      label: "研究报告",
      icon: Document,
      children: [{ index: "/reports", label: "报告管理" }],
    },
    {
      key: "watchlist",
      label: "自选关注",
      icon: Star,
      children: [{ index: "/watchlist", label: "自选研究" }],
    },
    {
      key: "data-center",
      label: "数据管理",
      icon: Monitor,
      adminOnly: true,
      children: [{ index: "/data-center", label: "数据源与同步任务" }],
    },
    {
      key: "settings",
      label: "系统设置",
      icon: Setting,
      children: [
        { index: "/settings", label: "资料与偏好" },
        { index: "/status", label: "运行状态" },
      ],
    },
  ]
  return items.filter((item) => !item.adminOnly || session.role === "admin")
})

/** 当前路由命中哪一个一级导航，用于展开该分组。 */
const activeGroup = computed(() => {
  const hit = groups.value.find((group) =>
    group.children.some((child) => child.index === route.path),
  )
  return hit ? [hit.key] : []
})

const accountLabel = computed(() => session.username ?? "未登录")
const authenticated = computed(() => Boolean(session.accessToken))

const dataTime = computed(() => (snapshot.value ? formatShanghai(snapshot.value.asOf) : "—"))
const taskHint = computed(() => {
  if (statusError.value) {
    return statusError.value
  }
  if (!snapshot.value) {
    return "任务服务读取中"
  }
  const worker = snapshot.value.worker
  if (worker.status === "up") {
    return snapshot.value.ready ? "任务服务正常" : "任务服务排队中"
  }
  return reasonText(worker.reason) ?? "任务服务不可用"
})

function switchTheme(): void {
  theme.value = applyTheme(toggleTheme(theme.value))
}

function reloadPage(): void {
  window.location.reload()
}

function openAccount(command: string): void {
  if (command === "logout") {
    void logout().finally(() => router.push("/login"))
    return
  }
  void router.push(command)
}

onMounted(() => {
  theme.value = applyTheme(readTheme(window.localStorage.getItem("finscope-theme")))
  void loadStatus()
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
      <div class="brand">
        <strong>{{ collapsed ? "FS" : "FinScope AI" }}</strong>
        <span v-if="!collapsed">不提供交易执行</span>
      </div>
      <el-menu
        class="side-menu"
        :default-active="route.path"
        :default-openeds="activeGroup"
        :collapse="collapsed"
        :collapse-transition="false"
        router
        unique-opened
      >
        <el-sub-menu v-for="group in groups" :key="group.key" :index="group.key">
          <template #title>
            <el-icon><component :is="group.icon" /></el-icon>
            <span>{{ group.label }}</span>
          </template>
          <el-menu-item v-for="child in group.children" :key="child.index" :index="child.index">
            {{ child.label }}
          </el-menu-item>
        </el-sub-menu>
      </el-menu>
      <p class="legend">
        <span class="rise">涨 +</span>
        <span class="fall">跌 −</span>
      </p>
      <button class="text-button rail-toggle" type="button" @click="collapsed = !collapsed">
        {{ collapsed ? "展开导航" : "折叠导航" }}
      </button>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <GlobalSearch />
        <div class="nav-links">
          <span class="topbar-chip muted" :title="statusError || '就绪检查数据时间'">
            数据时间 {{ dataTime }}
          </span>
          <span
            class="topbar-chip"
            :class="snapshot?.worker.status === 'up' ? 'up' : 'down'"
            :title="taskHint"
          >
            {{ taskHint }}
          </span>
          <button class="text-button" type="button" @click="reloadPage">
            <el-icon><Refresh /></el-icon>
            刷新
          </button>
          <button class="text-button" type="button" @click="switchTheme">
            {{ theme === "dark" ? "浅色" : "深色" }}
          </button>
          <el-dropdown trigger="click" @command="openAccount">
            <button class="text-button account" type="button">
              {{ accountLabel }}
              <el-icon><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="/settings">系统设置</el-dropdown-item>
                <el-dropdown-item v-if="session.role === 'admin'" command="/data-center">
                  数据管理
                </el-dropdown-item>
                <el-dropdown-item v-if="authenticated" command="logout" divided>
                  退出登录
                </el-dropdown-item>
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
.side-menu {
  flex: 1 1 auto;
  border-right: 0;
  background: transparent;
  overflow-y: auto;
}

.side-menu:not(.el-menu--collapse) {
  width: 100%;
}

/* 折叠到 72px 时菜单自身不再占 64px 固定宽，避免撑破侧栏。 */
.app-shell.collapsed .side-nav {
  padding: 16px 8px;
}

.app-shell.collapsed .side-menu,
.app-shell.collapsed .side-menu.el-menu--collapse {
  width: 100%;
}

.topbar-chip {
  font-size: 13px;
  white-space: nowrap;
}

.topbar-chip.up {
  color: var(--ok);
}

.topbar-chip.down {
  color: var(--warn);
}

.account {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

@media (max-width: 1279px) {
  .topbar-chip {
    display: none;
  }
}
</style>
