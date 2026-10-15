import { createRouter, createWebHistory, type RouteLocationRaw, type Router } from "vue-router"
import { restoreSession, session, type Role } from "../stores/auth"
import AssistantView from "../views/AssistantView.vue"
import CompanyView from "../views/CompanyView.vue"
import DataCenterView from "../views/DataCenterView.vue"
import FilingsView from "../views/FilingsView.vue"
import IndustryView from "../views/IndustryView.vue"
import ForbiddenView from "../views/ForbiddenView.vue"
import LibraryView from "../views/LibraryView.vue"
import LoginView from "../views/LoginView.vue"
import MetricsView from "../views/MetricsView.vue"
import NotFoundView from "../views/NotFoundView.vue"
import MacroView from "../views/MacroView.vue"
import QuotesView from "../views/QuotesView.vue"
import ReportView from "../views/ReportView.vue"
import SettingsView from "../views/SettingsView.vue"
import SimulationView from "../views/SimulationView.vue"
import TemperatureView from "../views/TemperatureView.vue"
import BoardView from "../views/BoardView.vue"
import SystemStatusView from "../views/SystemStatusView.vue"
import UploadView from "../views/UploadView.vue"

export interface NavigationSession {
  authenticated: boolean
  role: Role | null
}

export const routes = [
  { path: "/", name: "status", component: SystemStatusView },
  { path: "/macro", name: "macro", component: MacroView },
  { path: "/quotes", name: "quotes", component: QuotesView },
  { path: "/filings", name: "filings", component: FilingsView },
  { path: "/metrics", name: "metrics", component: MetricsView },
  { path: "/company", name: "company", component: CompanyView },
  { path: "/industries", name: "industries", component: IndustryView },
  {
    path: "/reports/:runId",
    name: "report",
    component: ReportView,
    meta: { requiresAuth: true },
  },
  { path: "/uploads", name: "uploads", component: UploadView, meta: { requiresAuth: true } },
  { path: "/assistant", name: "assistant", component: AssistantView, meta: { requiresAuth: true } },
  { path: "/library", name: "library", component: LibraryView, meta: { requiresAuth: true } },
  { path: "/temperature", name: "temperature", component: TemperatureView },
  { path: "/board", name: "board", component: BoardView },
  { path: "/simulation/mvp", name: "simulation", component: SimulationView, meta: { requiresAuth: true } },
  { path: "/login", name: "login", component: LoginView },
  { path: "/settings", name: "settings", component: SettingsView, meta: { requiresAuth: true } },
  {
    path: "/data-center",
    name: "data-center",
    component: DataCenterView,
    meta: { requiresAuth: true, requiresAdmin: true },
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
  router.beforeEach(async (to) => {
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
