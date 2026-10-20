import {
  createRouter,
  createWebHistory,
  type RouteLocationRaw,
  type Router,
} from "vue-router"
import { restoreSession, useSessionStore, type Role } from "../stores/auth"

export interface NavigationSession {
  authenticated: boolean
  role: Role | null
}

/**
 * 所有页面都是路由级懒加载（`() => import(...)`）：首屏只下载当前页用到的代码，
 * 其余页面和 ECharts 等大依赖在第一次访问时才加载，而不是打进一个 2.6 MB 的入口包。
 *
 * 路由表对齐规格书 §1.3（20 条主路由）。
 * 规格书未列、但已实现且有真实后端的页面挂在二级路径下，保持可访问，
 * 不删除既有能力：/status、/market/temperature、/research/board、
 * /simulation/mvp、/company/:symbol/metrics、/company/:symbol/uploads、
 * /filings/sec。
 */
export const routes = [
  { path: "/", redirect: "/dashboard" },
  { path: "/login", name: "login", component: () => import("../views/LoginView.vue") },

  // 总览驾驶舱（P0）
  { path: "/dashboard", name: "dashboard", component: () => import("../views/DashboardView.vue") },

  // 全球宏观（P0/P1）
  { path: "/macro/us", name: "macro-us", component: () => import("../views/MacroView.vue") },
  { path: "/macro/china", name: "macro-china", component: () => import("../views/MacroChinaView.vue") },
  { path: "/macro/liquidity", name: "macro-liquidity", component: () => import("../views/MacroLiquidityView.vue") },
  { path: "/macro/scenarios", name: "macro-scenarios", component: () => import("../views/MacroScenariosView.vue") },

  // 市场行情（P0/P1）
  { path: "/market/indices", name: "market-indices", component: () => import("../views/MarketIndicesView.vue") },
  { path: "/market/sectors", name: "market-sectors", component: () => import("../views/MarketSectorsView.vue") },
  { path: "/market/stocks/:symbol", name: "market-stock", component: () => import("../views/QuotesView.vue"), props: true },

  // 公司研究（P0/P1）
  { path: "/company/:symbol", name: "company", component: () => import("../views/CompanyView.vue"), props: true },
  {
    path: "/company/:symbol/filings",
    name: "company-filings",
    component: () => import("../views/CompanyFilingsView.vue"),
    props: true,
    meta: { requiresAuth: true },
  },
  {
    path: "/company/:symbol/analysis/:runId",
    name: "company-analysis",
    component: () => import("../views/CompanyAnalysisView.vue"),
    props: true,
    meta: { requiresAuth: true },
  },
  // 规格书未列、既有能力保留
  {
    path: "/company/:symbol/metrics",
    name: "company-metrics",
    component: () => import("../views/MetricsView.vue"),
    props: true,
  },
  {
    path: "/company/:symbol/uploads",
    name: "company-uploads",
    component: () => import("../views/UploadView.vue"),
    props: true,
    meta: { requiresAuth: true },
  },

  // 产业研究（P1）
  { path: "/industry", name: "industry", component: () => import("../views/IndustryView.vue") },
  { path: "/industry/pulse", name: "industry-pulse", component: () => import("../views/MarketPulseView.vue") },
  { path: "/industry/board/:code", name: "industry-board", component: () => import("../views/IndustryBoardView.vue"), props: true },
  { path: "/industry/:id", name: "industry-detail", component: () => import("../views/IndustryDetailView.vue"), props: true },

  // 热点（P2）
  { path: "/hotspots", name: "hotspots", component: () => import("../views/HotspotsView.vue") },

  // AI 问答（P1）
  { path: "/assistant", name: "assistant", component: () => import("../views/AssistantView.vue"), meta: { requiresAuth: true } },

  // 自定义数据分析（规格书未列，用户自加；数据全部来自公开页面，无需登录）
  { path: "/custom-analysis", name: "custom-analysis", component: () => import("../views/CustomAnalysisView.vue") },

  // 报告管理（P1）
  { path: "/reports", name: "reports", component: () => import("../views/LibraryView.vue"), meta: { requiresAuth: true } },
  {
    path: "/reports/:runId",
    name: "report",
    component: () => import("../views/ReportView.vue"),
    meta: { requiresAuth: true },
  },

  // 自选研究（P1）
  { path: "/watchlist", name: "watchlist", component: () => import("../views/WatchlistView.vue"), meta: { requiresAuth: true } },

  // 数据管理（P0，仅 admin）
  {
    path: "/data-center",
    name: "data-center",
    component: () => import("../views/DataCenterView.vue"),
    meta: { requiresAuth: true, requiresAdmin: true },
  },

  // 设置（P0）
  { path: "/settings", name: "settings", component: () => import("../views/SettingsView.vue"), meta: { requiresAuth: true } },

  // 规格书未列、既有能力保留
  { path: "/status", name: "status", component: () => import("../views/SystemStatusView.vue") },
  { path: "/market/temperature", name: "temperature", component: () => import("../views/TemperatureView.vue") },
  { path: "/research/board", name: "board", component: () => import("../views/BoardView.vue") },
  {
    path: "/simulation/mvp",
    name: "simulation",
    component: () => import("../views/SimulationView.vue"),
    meta: { requiresAuth: true },
  },
  { path: "/forbidden", name: "forbidden", component: () => import("../views/ForbiddenView.vue"), meta: { requiresAuth: true } },
  { path: "/:pathMatch(.*)*", name: "not-found", component: () => import("../views/NotFoundView.vue") },
]

export function decideNavigation(
  to: { fullPath: string; meta: { requiresAuth?: boolean; requiresAdmin?: boolean } },
  current: NavigationSession,
): true | RouteLocationRaw {
  const needsAuth = Boolean(to.meta.requiresAuth || to.meta.requiresAdmin)
  if (needsAuth && !current.authenticated) {
    return { name: "login", query: { redirect: to.fullPath } }
  }
  if (to.meta.requiresAdmin && current.role !== "admin") {
    return { name: "forbidden" }
  }
  return true
}

export function installGuard(router: Router, restore: () => Promise<boolean> = restoreSession): void {
  let bootstrapped = false
  router.beforeEach(async (to) => {
    const session = useSessionStore()
    // 刷新页面后内存里的 access token 必然为空，只能靠 HttpOnly 的 refresh cookie 恢复。
    // 规格书 §1.3 里 /company/:symbol、/market/stocks/:symbol 这些页面并不要求登录，
    // 但页内的「保存研究记录」等动作需要身份：所以启动后第一次导航无论去哪都先恢复一次，
    // 否则刷新一个公开页面会误显示成未登录。
    if (!bootstrapped) {
      bootstrapped = true
      if (!session.accessToken) {
        await restore()
      }
    }
    if ((to.meta.requiresAuth || to.meta.requiresAdmin) && !session.accessToken) {
      const restored = await restore()
      if (!restored) {
        return { name: "login", query: { redirect: to.fullPath } }
      }
    }
    return decideNavigation(to, {
      authenticated: Boolean(session.accessToken),
      role: session.role,
    })
  })
}

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

installGuard(router)
