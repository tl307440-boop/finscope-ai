<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { Brush, Lock, Moon, Refresh, Setting, User } from "@element-plus/icons-vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, authorizedFetch, readJson } from "../api/client"
import { changePassword, logout, passwordHint, restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface Profile {
  username: string
  email: string
  role: string
  theme: string
  riseColorScheme: string
  timezone: string
  defaultMarket: string
  defaultLookback: string
}

const router = useRouter()
const phase = ref<"loading" | "ready" | "error">("loading")
const profile = ref<Profile | null>(null)
const failure = ref("无法读取账号")

const ROLE_TEXT: Record<string, string> = {
  researcher: "研究者",
  admin: "管理员",
}

const RISE_TEXT: Record<string, string> = {
  cn: "红涨绿跌",
  us: "绿涨红跌",
}

const MARKET_TEXT: Record<string, string> = {
  CN: "A 股",
  US: "美股",
  HK: "港股",
}

const LOOKBACK_TEXT: Record<string, string> = {
  "1m": "近 1 个月",
  "3m": "近 3 个月",
  "1y": "近 1 年",
  all: "全部历史",
}

const TIMEZONE_TEXT: Record<string, string> = {
  "Asia/Shanghai": "北京时间",
  "America/New_York": "美东时间",
  UTC: "协调世界时",
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
      riseColorScheme: asString(preferences?.rise_color_scheme) ?? "",
      timezone: asString(preferences?.timezone) ?? "",
      defaultMarket: asString(preferences?.default_market) ?? "",
      defaultLookback: asString(preferences?.default_lookback) ?? "",
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  }
}

async function signOut(): Promise<void> {
  await logout()
  await router.push("/login")
}

const isAdmin = computed<boolean>(() => profile.value?.role === "admin")

const roleLabel = computed<string>(() => {
  const role = profile.value?.role ?? ""
  return ROLE_TEXT[role] ?? "其他角色"
})

const roleCaption = computed<string>(() =>
  isAdmin.value ? "可使用全部研究功能，并管理数据源与同步任务" : "可使用全部研究功能；数据中心仅对管理员开放",
)

const themeText = computed<string>(() => (profile.value?.theme === "light" ? "浅色" : "深色"))

const pwCurrent = ref("")
const pwNew = ref("")
const pwConfirm = ref("")
const pwBusy = ref(false)
const pwNote = ref<{ ok: boolean; text: string } | null>(null)

async function submitPassword(): Promise<void> {
  pwNote.value = null
  const problem =
    pwCurrent.value.length === 0
      ? "请输入当前密码"
      : passwordHint(pwNew.value) ?? (pwNew.value === pwConfirm.value ? null : "两次输入的新密码不一致")
  if (problem) {
    pwNote.value = { ok: false, text: problem }
    return
  }
  pwBusy.value = true
  try {
    await changePassword(pwCurrent.value, pwNew.value)
    pwCurrent.value = ""
    pwNew.value = ""
    pwConfirm.value = ""
    pwNote.value = { ok: true, text: "密码已修改，其它设备上的登录已失效" }
  } catch (caught) {
    pwNote.value = { ok: false, text: caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试" }
  } finally {
    pwBusy.value = false
  }
}
function mapped(table: Record<string, string>, value: string | undefined): string {
  if (!value) {
    return "未设置"
  }
  return table[value] ?? value
}

const preferenceRows = computed(() => {
  const current = profile.value
  if (!current) {
    return []
  }
  const zone = current.timezone
  return [
    { label: "涨跌配色", value: mapped(RISE_TEXT, current.riseColorScheme) },
    { label: "时区", value: zone ? `${TIMEZONE_TEXT[zone] ?? zone}（${zone}）` : "未设置" },
    { label: "默认市场", value: mapped(MARKET_TEXT, current.defaultMarket) },
    { label: "默认回看区间", value: mapped(LOOKBACK_TEXT, current.defaultLookback) },
  ]
})

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
    <PageHeader title="账号设置" subtitle="查看当前登录账号、角色权限与研究偏好">
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
        <div class="grid-3">
          <StatCard title="账号" :value="profile.username" :caption="profile.email" tone="accent">
            <template #icon><el-icon><User /></el-icon></template>
          </StatCard>

          <StatCard title="角色" :value="roleLabel" :caption="roleCaption" info="账号角色决定可以使用的功能" tone="violet">
            <template #icon><el-icon><Lock /></el-icon></template>
            <template #below>
              <StatusPill
                class="below-pill"
                :text="isAdmin ? '管理员权限' : '研究者权限'"
                :tone="isAdmin ? 'accent' : 'muted'"
                dot
              />
            </template>
          </StatCard>

          <StatCard
            title="界面主题"
            :value="themeText"
            caption="可在顶部栏切换深浅主题，切换结果只保存在当前浏览器"
            tone="cyan"
          >
            <template #icon><el-icon><Moon /></el-icon></template>
          </StatCard>
        </div>

        <div class="grid-2">
          <PanelCard title="研究偏好" info="账号保存的默认偏好">
            <template #icon><el-icon><Brush /></el-icon></template>
            <dl class="kv">
              <template v-for="item in preferenceRows" :key="item.label">
                <dt>{{ item.label }}</dt>
                <dd>{{ item.value }}</dd>
              </template>
            </dl>
          </PanelCard>

          <PanelCard title="登录与安全">
            <template #icon><el-icon><Setting /></el-icon></template>
            <template #actions>
              <router-link
                v-if="isAdmin"
                class="ghost-button"
                to="/data-center"
                aria-label="打开数据源管理"
              >数据源管理 ›</router-link>
            </template>
            <dl class="kv">
              <dt>登录方式</dt>
              <dd>账号密码登录，登录状态过期后会自动续期一次</dd>
              <dt>退出登录</dt>
              <dd>点右上角「退出登录」会同时清除本机与服务器上的登录状态</dd>
            </dl>
          </PanelCard>
        </div>

        <PanelCard title="修改密码" info="修改后，其它设备上的登录会立刻失效；本机保持登录">
          <template #icon><el-icon><Lock /></el-icon></template>
          <form class="pw-form" @submit.prevent="submitPassword">
            <el-input v-model="pwCurrent" type="password" show-password autocomplete="current-password" placeholder="当前密码" aria-label="当前密码" />
            <el-input v-model="pwNew" type="password" show-password autocomplete="new-password" placeholder="新密码（至少 8 位，含字母和数字）" aria-label="新密码" />
            <el-input v-model="pwConfirm" type="password" show-password autocomplete="new-password" placeholder="再输入一次新密码" aria-label="确认新密码" />
            <div class="pw-actions">
              <el-button type="primary" native-type="submit" :loading="pwBusy">修改密码</el-button>
              <span v-if="pwNote" class="pw-note" :class="pwNote.ok ? 'ok' : 'bad'" role="status">{{ pwNote.text }}</span>
            </div>
          </form>
        </PanelCard>
      </template>
    </div>
  </main>
</template>

<style scoped>
.kv {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  margin: 0;
  font-size: 13px;
}

.kv dt,
.kv dd {
  padding: 10px 0;
  border-top: 1px solid var(--line-soft);
}

.kv dt:first-of-type,
.kv dd:first-of-type {
  border-top: 0;
  padding-top: 2px;
}

.kv dt {
  color: var(--muted);
}

.kv dd {
  margin: 0;
  color: var(--text);
  line-height: 1.6;
}

.pw-form {
  display: grid;
  gap: 10px;
  max-width: 420px;
}

.pw-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.pw-note {
  font-size: 13px;
}

.pw-note.ok {
  color: var(--ok);
}

.pw-note.bad {
  color: var(--danger);
}

.below-pill {
  margin-top: 8px;
}

.ghost-button {
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
}

.ghost-button:hover {
  text-decoration: underline;
}

@media (max-width: 767px) {
  .kv {
    grid-template-columns: minmax(0, 1fr);
  }

  .kv dd {
    border-top: 0;
    padding-top: 0;
  }
}
</style>
