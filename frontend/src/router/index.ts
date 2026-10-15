import { createRouter, createWebHistory, type RouteLocationRaw, type Router } from "vue-router"
import { restoreSession, session, type Role } from "../stores/auth"
import DataCenterView from "../views/DataCenterView.vue"
import FilingsView from "../views/FilingsView.vue"
import ForbiddenView from "../views/ForbiddenView.vue"
import LoginView from "../views/LoginView.vue"
import MetricsView from "../views/MetricsView.vue"
import NotFoundView from "../views/NotFoundView.vue"
import MacroView from "../views/MacroView.vue"
import QuotesView from "../views/QuotesView.vue"
import SettingsView from "../views/SettingsView.vue"
import SystemStatusView from "../views/SystemStatusView.vue"

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
