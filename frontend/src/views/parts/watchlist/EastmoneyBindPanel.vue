<script setup lang="ts">
/**
 * 东方财富绑定卡片：扫码登录后，把东财自选（全部分组）单向同步进来，并在页面打开期间每分钟检查一次。
 *
 * - 同步方向是「东财 → 本系统」；同步来的条目在东财里删掉后才会在这里移除，手动加的条目不受影响。
 * - 登录凭证只在后端加密保存；解除绑定会立即删除，已同步的自选保留。
 * - 东财登录失效时显示「需要重新扫码」，不会静默失败。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { Connection } from "@element-plus/icons-vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import {
  fetchBinding,
  pollQr,
  startQr,
  syncNow,
  unbind,
  type BindingStatus,
  type QrCode,
  type SyncResult,
} from "../../../api/eastmoneyBinding"
import { restoreSession, useSessionStore } from "../../../stores/auth"

const emit = defineEmits<{ (event: "synced"): void }>()

const session = useSessionStore()
const POLL_MS = 2000
const AUTO_SYNC_MS = 60_000

const status = ref<BindingStatus | null>(null)
const loadError = ref("")
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)

const dialogOpen = ref(false)
const qr = ref<QrCode | null>(null)
const qrPhase = ref<"idle" | "loading" | "waiting" | "expired" | "error">("idle")
const qrError = ref("")
const secondsLeft = ref(0)

let pollTimer: ReturnType<typeof setTimeout> | null = null
let countdownTimer: ReturnType<typeof setInterval> | null = null
let autoTimer: ReturnType<typeof setInterval> | null = null
let pollFailures = 0

const bound = computed<boolean>(() => Boolean(status.value?.bound))
const expired = computed<boolean>(() => status.value?.status === "expired")

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error && caught.message ? caught.message : fallback
}

function formatTime(iso: string): string {
  if (!iso) {
    return "尚未同步"
  }
  const moment = new Date(iso)
  if (Number.isNaN(moment.getTime())) {
    return iso
  }
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`
}

const syncCaption = computed<string>(() => {
  const current = status.value
  if (!current?.bound) {
    return ""
  }
  const parts = [`最近同步 ${formatTime(current.lastSyncAt)}`]
  if (current.groups !== null && current.items !== null) {
    parts.push(`${current.groups} 个分组、${current.items} 个标的`)
  }
  return parts.join(" · ")
})

async function loadStatus(): Promise<void> {
  loadError.value = ""
  try {
    status.value = await fetchBinding(session.accessToken, refreshToken)
  } catch (caught) {
    loadError.value = messageOf(caught, "读取绑定状态失败")
  }
}

function announce(result: SyncResult): void {
  actionOk.value = result.cached ? "刚同步过，已是最新" : result.message
  if (!result.cached && (result.added || result.removed || result.groups)) {
    emit("synced")
  }
}

async function runSync(manual: boolean): Promise<void> {
  if (busy.value) {
    return
  }
  busy.value = manual
  if (manual) {
    actionError.value = ""
    actionOk.value = ""
  }
  try {
    const result = await syncNow(session.accessToken, refreshToken)
    if (manual || result.added || result.removed) {
      announce(result)
    }
    await loadStatus()
  } catch (caught) {
    // 登录失效时状态会变成 expired，卡片会提示重新扫码。
    if (manual) {
      actionError.value = messageOf(caught, "同步失败")
    }
    await loadStatus()
  } finally {
    busy.value = false
  }
}

function stopQrTimers(): void {
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  if (countdownTimer !== null) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

async function openQr(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  dialogOpen.value = true
  await refreshQr()
}

async function refreshQr(): Promise<void> {
  stopQrTimers()
  qrError.value = ""
  qrPhase.value = "loading"
  pollFailures = 0
  try {
    const code = await startQr(session.accessToken, refreshToken)
    qr.value = code
    qrPhase.value = "waiting"
    secondsLeft.value = code.expiresIn
    countdownTimer = setInterval(() => {
      secondsLeft.value = Math.max(0, secondsLeft.value - 1)
    }, 1000)
    schedulePoll(code.loginId)
  } catch (caught) {
    qr.value = null
    qrPhase.value = "error"
    qrError.value = messageOf(caught, "二维码生成失败")
  }
}

function schedulePoll(loginId: string): void {
  pollTimer = setTimeout(() => void pollOnce(loginId), POLL_MS)
}

async function pollOnce(loginId: string): Promise<void> {
  if (!dialogOpen.value || qr.value?.loginId !== loginId) {
    return
  }
  try {
    const state = await pollQr(loginId, session.accessToken, refreshToken)
    pollFailures = 0
    if (state.phase === "success") {
      stopQrTimers()
      dialogOpen.value = false
      qr.value = null
      qrPhase.value = "idle"
      await loadStatus()
      if (state.sync) {
        announce(state.sync)
        emit("synced")
      } else {
        actionError.value = state.syncError || "已绑定，但首次同步失败，请稍后点「立即同步」"
      }
      return
    }
    if (state.phase === "expired") {
      stopQrTimers()
      qrPhase.value = "expired"
      return
    }
  } catch (caught) {
    pollFailures += 1
    if (pollFailures >= 5) {
      stopQrTimers()
      qrPhase.value = "error"
      qrError.value = messageOf(caught, "查询扫码状态失败")
      return
    }
  }
  schedulePoll(loginId)
}

watch(dialogOpen, (open) => {
  if (!open) {
    stopQrTimers()
    qr.value = null
    qrPhase.value = "idle"
  }
})

async function removeBinding(): Promise<void> {
  if (busy.value) {
    return
  }
  busy.value = true
  actionError.value = ""
  actionOk.value = ""
  try {
    await unbind(session.accessToken, refreshToken)
    actionOk.value = "已解除绑定，登录凭证已删除；已同步的自选保留在各分组里"
    await loadStatus()
  } catch (caught) {
    actionError.value = messageOf(caught, "解除绑定失败")
  } finally {
    busy.value = false
  }
}

function startAutoSync(): void {
  autoTimer = setInterval(() => {
    if (document.visibilityState === "visible" && status.value?.bound && !expired.value && !dialogOpen.value) {
      void runSync(false)
    }
  }, AUTO_SYNC_MS)
}

onMounted(async () => {
  await loadStatus()
  if (status.value?.bound && !expired.value) {
    void runSync(false)
  }
  startAutoSync()
})

onBeforeUnmount(() => {
  stopQrTimers()
  if (autoTimer !== null) {
    clearInterval(autoTimer)
  }
})
</script>

<template>
  <PanelCard
    title="东方财富同步"
    info="用东方财富 App 扫码登录后，把你在东财的全部自选分组同步进来（东财 → 本系统，单向）。页面打开时每分钟检查一次；东财里新增或删除的标的会跟着变，你在这里手动加的标的不会被动。登录凭证加密保存在服务端，随时可以解除绑定。"
  >
    <template #icon><el-icon><Connection /></el-icon></template>

    <p v-if="loadError" class="note danger">{{ loadError }}</p>

    <div v-else-if="status && !bound" class="bind-row">
      <p class="note first">还没有绑定。扫码一次，东财自选的分组、板块、美股、ETF 都会同步过来，之后自动更新。</p>
      <el-button type="primary" :loading="qrPhase === 'loading'" @click="openQr">绑定东方财富</el-button>
    </div>

    <div v-else-if="status" class="bound">
      <div class="bound-head">
        <StatusPill
          :tone="expired ? 'warn' : 'ok'"
          :text="expired ? '登录已失效' : '已绑定'"
          dot
        />
        <strong class="alias">{{ status.alias || "东方财富账号" }}</strong>
        <span v-if="!expired" class="caption">{{ syncCaption }}</span>
      </div>
      <p v-if="expired" class="note danger">东财登录已失效，需要重新扫码；已同步的自选不受影响。</p>
      <p v-else-if="status.message" class="note">{{ status.message }}</p>
      <div class="actions">
        <el-button v-if="!expired" :loading="busy" @click="runSync(true)">立即同步</el-button>
        <el-button :type="expired ? 'primary' : 'default'" :disabled="busy" @click="openQr">
          {{ expired ? "重新扫码" : "换账号 / 重新扫码" }}
        </el-button>
        <el-button text type="danger" :disabled="busy" @click="removeBinding">解除绑定</el-button>
      </div>
    </div>

    <p v-else class="note first">正在读取绑定状态…</p>

    <p v-if="actionOk" class="note ok">{{ actionOk }}</p>
    <p v-if="actionError" class="note danger">{{ actionError }}</p>

    <el-dialog v-model="dialogOpen" title="扫码绑定东方财富" width="360px" append-to-body>
      <div class="qr-box">
        <template v-if="qrPhase === 'loading'">
          <p class="note">正在生成二维码…</p>
        </template>
        <template v-else-if="qrPhase === 'waiting' && qr">
          <img class="qr-img" :src="qr.imageSrc" alt="东方财富登录二维码" />
          <p class="note">打开东方财富 App，首页右上角 ＋ → 扫一扫，扫码后在手机上确认登录</p>
          <p class="caption">二维码 {{ secondsLeft }} 秒后失效</p>
        </template>
        <template v-else-if="qrPhase === 'expired'">
          <p class="note danger">二维码已失效</p>
          <el-button type="primary" @click="refreshQr">重新生成</el-button>
        </template>
        <template v-else-if="qrPhase === 'error'">
          <p class="note danger">{{ qrError }}</p>
          <el-button type="primary" @click="refreshQr">重试</el-button>
        </template>
      </div>
    </el-dialog>
  </PanelCard>
</template>

<style scoped>
.bind-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.bound {
  display: grid;
  gap: 8px;
}

.bound-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}

.alias {
  font-weight: 600;
}

.caption {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.note {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.note.first {
  margin-top: 0;
}

.note.danger {
  color: var(--danger);
}

.note.ok {
  color: var(--ok);
}

.qr-box {
  display: grid;
  justify-items: center;
  gap: 10px;
  text-align: center;
}

.qr-img {
  width: 220px;
  height: 220px;
  image-rendering: pixelated;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: #fff;
  padding: 8px;
}
</style>
