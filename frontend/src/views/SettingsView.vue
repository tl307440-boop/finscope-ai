<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { ApiError, authorizedFetch, readJson } from "../api/client"
import { logout, restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface Profile {
  username: string
  email: string
  role: string
  theme: string
}

const router = useRouter()
const phase = ref<"loading" | "ready" | "error">("loading")
const profile = ref<Profile | null>(null)
const failure = ref("无法读取账号")

const roleText: Record<string, string> = {
  researcher: "研究者",
  admin: "管理员",
}

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await authorizedFetch("/api/v1/me", { method: "GET" }, session.accessToken, async () => {
      const restored = await restoreSession()
      return restored ? session.accessToken : null
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw new ApiError("无法读取账号", "HTTP_ERROR", response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const user = asRecord(data?.user)
    const preferences = asRecord(data?.preferences)
    profile.value = {
      username: asString(user?.username) ?? "—",
      email: asString(user?.email) ?? "—",
      role: asString(user?.role) ?? "researcher",
      theme: asString(preferences?.theme) ?? "dark",
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function signOut(): Promise<void> {
  await logout()
  await router.push("/login")
}

onMounted(() => {
  void load()
})

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取账号</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取账号</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>
    <section v-else-if="profile" class="panel">
      <h1>账号设置</h1>
      <p>账号 {{ profile.username }}</p>
      <p>邮箱 {{ profile.email }}</p>
      <p>角色 {{ roleText[profile.role] ?? profile.role }}</p>
      <p class="muted">主题 {{ profile.theme === "light" ? "浅色" : "深色" }}</p>
      <div class="action-row">
        <router-link
          v-if="profile.role === 'admin'"
          class="retry link-button"
          to="/data-center"
        >
          打开数据源管理
        </router-link>
        <button class="retry" type="button" @click="signOut">退出</button>
      </div>
    </section>
  </main>
</template>
