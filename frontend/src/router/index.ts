import { createRouter, createWebHistory } from "vue-router"
import NotFoundView from "../views/NotFoundView.vue"
import SystemStatusView from "../views/SystemStatusView.vue"

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "status", component: SystemStatusView },
    { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundView },
  ],
})
