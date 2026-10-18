import {
  createRouter,
  createWebHistory,
  type RouteLocationRaw,
  type Router,
} from "vue-router"
import { restoreSession, useSessionStore, type Role } from "../stores/auth"
import AssistantView from "../views/AssistantView.vue"
import BoardView from "../views/BoardView.vue"
import CompanyAnalysisView from "../views/CompanyAnalysisView.vue"
import CompanyFilingsView from "../views/CompanyFilingsView.vue"
import CompanyView from "../views/CompanyView.vue"
import CustomAnalysisView from "../views/CustomAnalysisView.vue"
import DashboardView from "../views/DashboardView.vue"
import DataCenterView from "../views/DataCenterView.vue"
import IndustryBoardView from "../views/IndustryBoardView.vue"
import IndustryDetailView from "../views/IndustryDetailView.vue"
import IndustryView from "../views/IndustryView.vue"
import ForbiddenView from "../views/ForbiddenView.vue"
import HotspotsView from "../views/HotspotsView.vue"
import LibraryView from "../views/LibraryView.vue"
import LoginView from "../views/LoginView.vue"
import MacroChinaView from "../views/MacroChinaView.vue"
import MacroLiquidityView from "../views/MacroLiquidityView.vue"
import MacroScenariosView from "../views/MacroScenariosView.vue"
import MacroView from "../views/MacroView.vue"
import MarketIndicesView from "../views/MarketIndicesView.vue"
import MarketPulseView from "../views/MarketPulseView.vue"
import MarketSectorsView from "../views/MarketSectorsView.vue"
import MetricsView from "../views/MetricsView.vue"
import NotFoundView from "../views/NotFoundView.vue"
import QuotesView from "../views/QuotesView.vue"
import ReportView from "../views/ReportView.vue"
import SettingsView from "../views/SettingsView.vue"
import SimulationView from "../views/SimulationView.vue"
import SystemStatusView from "../views/SystemStatusView.vue"
import TemperatureView from "../views/TemperatureView.vue"
import UploadView from "../views/UploadView.vue"
import WatchlistView from "../views/WatchlistView.vue"

export interface NavigationSession {
  authenticated: boolean
  role: Role | null
}

/**
 * 路由表对齐规格书 §1.3（20 条主路由）。
 * 规格书未列、但已实现且有真实后端的页面挂在二级路径下，保持可访问，
 * 不删除既有能力：/status、/market/temperature、/research/board、
 * /simulation/mvp、/company/:symbol/metrics、/company/:symbol/uploads、
 * /filings/sec。
 */
export const routes = [
  { path: "/", redirect: "/dashboard" },
  { path: "/login", name: "login", component: LoginView },

  // 总览驾驶舱（P0）
  { path: "/dashboard", name: "dashboard", component: DashboardView },

  // 全球宏观（P0/P1）
  { path: "/macro/us", name: "macro-us", component: MacroView },
  { path: "/macro/china", name: "macro-china", component: MacroChinaView },
  { path: "/macro/liquidity", name: "macro-liquidity", component: MacroLiquidityView },
  { path: "/macro/scenarios", name: "macro-scenarios", component: MacroScenariosView },

  // 市场行情（P0/P1）
  { path: "/market/indices", name: "market-indices", component: MarketIndicesView },
  { path: "/market/sectors", name: "market-sectors", component: MarketSectorsView },
  { path: "/market/stocks/:symbol", name: "market-stock", component: QuotesView, props: true },

  // 公司研究（P0/P1）
  { path: "/company/:symbol", name: "company", component: CompanyView, props: true },
  {
    path: "/company/:symbol/filings",
    name: "company-filings",
    component: CompanyFilingsView,
    props: true,
    meta: { requiresAuth: true },
  },
  {
    path: "/company/:symbol/analysis/:runId",
    name: "company-analysis",
    component: CompanyAnalysisView,
    props: true,
    meta: { requiresAuth: true },
  },
  // 规格书未列、既有能力保留
  {
    path: "/company/:symbol/metrics",
    name: "company-metrics",
    component: MetricsView,
    props: true,
  },
  {
    path: "/company/:symbol/uploads",
    name: "company-uploads",
    component: UploadView,
    props: true,
    meta: { requiresAuth: true },
  },

  // 产业研究（P1）
  { path: "/industry", name: "industry", component: IndustryView },
  { path: "/industry/pulse", name: "industry-pulse", component: MarketPulseView },
  { path: "/industry/board/:code", name: "industry-board", component: IndustryBoardView, props: true },
  { path: "/industry/:id", name: "industry-detail", component: IndustryDetailView, props: true },

  // 热点（P2）
  { path: "/hotspots", name: "hotspots", component: HotspotsView },

  // AI 问答（P1）
  { path: "/assistant", name: "assistant", component: AssistantView, meta: { requiresAuth: true } },

  // 自定义数据分析（规格书未列，用户自加；数据全部来自公开页面，无需登录）
  { path: "/custom-analysis", name: "custom-analysis", component: CustomAnalysisView },

  // 报告管理（P1）
  { path: "/reports", name: "reports", component: LibraryView, meta: { requiresAuth: true } },
  {
    path: "/reports/:runId",
    name: "report",
    component: ReportView,
    meta: { requiresAuth: true },
  },

  // 自选研究（P1）
  { path: "/watchlist", name: "watchlist", component: WatchlistView, meta: { requiresAuth: true } },

  // 数据管理（P0，仅 admin）
  {
    path: "/data-center",
    name: "data-center",
    component: DataCenterView,
    meta: { requiresAuth: true, requiresAdmin: true },
  },

  // 设置（P0）
  { path: "/settings", name: "settings", component: SettingsView, meta: { requiresAuth: true } },

  // 规格书未列、既有能力保留
  { path: "/status", name: "status", component: SystemStatusView },
  { path: "/market/temperature", name: "temperature", component: TemperatureView },
  { path: "/research/board", name: "board", component: BoardView },
  {
    path: "/simulation/mvp",
    name: "simulation",
    component: SimulationView,
    meta: { requiresAuth: true },
  },
  { path: "/forbidden", name: "forbidden", component: ForbiddenView, meta: { requiresAuth: true } },
  { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundView },
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
