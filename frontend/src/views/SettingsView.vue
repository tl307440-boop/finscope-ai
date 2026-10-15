<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { Lock, Moon, Refresh, User } from "@element-plus/icons-vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
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

function themeText(): string {
  return profile.value?.theme === "light" ? "浅色" : "深色"
}

function roleLabel(): string {
  const role = profile.value?.role ?? ""
  return roleText[role] ?? role
}

function isAdmin(): boolean {
  return profile.value?.role === "admin"
}

function passwordNote(): string {
  return isAdmin()
    ? "暂无自助改密接口：管理员账号由部署侧脚本维护。"
    : "本页没有改密入口：当前后端只提供登录/刷新/退出三个账号接口。"
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
  <main id="main" class="page">
    <PageHeader title="账号设置" subtitle="查看当前登录账号、角色与界面主题">
      <template #actions>
        <el-button :loading="phase === 'loading'" :icon="Refresh" @click="load">刷新</el-button>
        <el-button type="primary" @click="signOut">退出登录</el-button>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        :rows="4"
      />
      <StateBlock
        v-else-if="phase === 'error' || !profile"
        state="error"
        title="无法读取账号"
        :message="failure"
        @retry="load"
      />
      <template v-else>
        <div class="grid-2">
          <PanelCard title="账号信息" info="来自 GET /api/v1/me 的 user 字段">
            <template #icon><el-icon><User /></el-icon></template>
            <dl class="kv">
              <dt>账号</dt>
              <dd>{{ profile.username }}</dd>
              <dt>邮箱</dt>
              <dd class="break">{{ profile.email }}</dd>
            </dl>
            <p class="note">
              这份信息由服务端返回，前端不维护任何本地副本；刷新即重新拉取，登录态失效时会自动续期一次。
            </p>
          </PanelCard>

          <PanelCard title="登录与凭据" info="本页展示的能力边界说明">
            <template #icon><el-icon><Lock /></el-icon></template>
            <dl class="kv">
              <dt>登录方式</dt>
              <dd>账号密码 + Bearer 访问令牌（凭据走 HttpOnly Cookie 续期）</dd>
              <dt>改密入口</dt>
              <dd class="break">{{ passwordNote() }}</dd>
            </dl>
            <p class="note">
              后端只暴露登录、刷新、退出三个账号相关接口，因此这里不提供修改邮箱或密码的表单，避免出现点了没反应的按钮。
            </p>
            <template #actions>
              <router-link
                v-if="isAdmin()"
                class="ghost-button"
                to="/data-center"
                aria-label="打开数据源管理"
              >数据源管理</router-link>
            </template>
          </PanelCard>
        </div>

        <div class="grid-3">
          <StatCard
            title="角色"
            :value="roleLabel()"
            caption="决定能否访问 /api/v1/admin/* 与数据源管理页"
            info="来自 GET /api/v1/me 的 user.role 字段"
            more-text=""
          >
            <template #icon><el-icon><User /></el-icon></template>
            <template #below>
              <StatusPill
                :text="isAdmin() ? '管理员权限已开通' : '研究者权限'"
                :tone="isAdmin() ? 'accent' : 'muted'"
                dot
              />
            </template>
          </StatCard>

          <StatCard
            title="界面主题"
            :value="themeText()"
            caption="跟随账号偏好返回，仅深浅两套"
            info="来自 GET /api/v1/me 的 preferences.theme 字段"
            more-text=""
          >
            <template #icon><el-icon><Moon /></el-icon></template>
            <template #below>
              <p class="note tight">
                主题由浏览器本地偏好（<code>localStorage</code> 的 <code>data-theme</code>）决定；
                账号侧的偏好值目前没有写入接口，所以这里只读不写。
              </p>
            </template>
          </StatCard>

          <StatCard
            title="本次加载"
            value="已载入"
            caption="令牌失效时本页请求会自动续期一次"
            info="authorizedFetch 在 401 时调用 /api/v1/auth/refresh 重试"
            more-text=""
          >
            <template #icon><el-icon><Lock /></el-icon></template>
            <template #below>
              <p class="note tight">
                这里只表示 <code>/api/v1/me</code> 刚刚返回成功：接口并不回报令牌过期时间，
                所以不显示剩余时长。退出登录会先请求服务端清除会话，再跳转 <code>/login</code>。
              </p>
            </template>
          </StatCard>
        </div>
      </template>
    </div>
  </main>
</template>

<style scoped>
.stack {
  margin-top: var(--gap);
}

.kv {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 10px 16px;
  margin: 0;
  font-size: 13px;
}

.kv dt {
  color: var(--muted);
}

.kv dd {
  margin: 0;
  color: var(--text);
}

.break {
  word-break: break-all;
}

.note {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.note.tight {
  margin: 0;
}

.note code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 11px;
}

@media (max-width: 767px) {
  .kv {
    grid-template-columns: minmax(0, 1fr);
    gap: 2px 0;
  }

  .kv dd {
    margin-bottom: 8px;
  }
}
</style>
