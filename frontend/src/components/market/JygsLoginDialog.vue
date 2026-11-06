<script setup lang="ts">
/**
 * 韭研公社扫码登录（仅管理员）：显示微信二维码，轮询扫码进度；
 * 成功后会话由后端加密保存，这里只通知父组件刷新。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue"

import { ApiError } from "../../api/client"
import {
  pollJygsLogin,
  startJygsLogin,
  type JygsLoginStatus,
} from "../../api/jygsLogin"
import { restoreSession, useSessionStore } from "../../stores/auth"

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void
  (event: "success"): void
}>()

const POLL_MS = 1500
const session = useSessionStore()

const qrImage = ref("")
const status = ref<JygsLoginStatus | "loading" | "error">("loading")
const message = ref("")
let loginId = ""
let timer: number | null = null
let generation = 0

const finished = computed(() =>
  ["expired", "cancelled", "failed", "error"].includes(status.value),
)

function refresh(): () => Promise<string | null> {
  return async () => ((await restoreSession()) ? session.accessToken : null)
}

function stop(): void {
  generation += 1
  if (timer !== null) {
    window.clearTimeout(timer)
    timer = null
  }
}

function describe(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
}

async function begin(): Promise<void> {
  stop()
  const current = generation
  status.value = "loading"
  message.value = "正在获取二维码…"
  qrImage.value = ""
  try {
    const started = await startJygsLogin(session.accessToken, refresh())
    if (current !== generation) return
    loginId = started.loginId
    qrImage.value = started.qrImage
    status.value = "waiting"
    message.value = "请用微信扫描二维码"
    schedule(current)
  } catch (caught) {
    if (current !== generation) return
    status.value = "error"
    message.value = describe(caught)
  }
}

function schedule(current: number): void {
  timer = window.setTimeout(() => void tick(current), POLL_MS)
}

async function tick(current: number): Promise<void> {
  if (current !== generation) return
  try {
    const progress = await pollJygsLogin(loginId, session.accessToken, refresh())
    if (current !== generation) return
    status.value = progress.status
    message.value = progress.message
    if (progress.status === "confirmed") {
      emit("success")
      window.setTimeout(() => emit("update:modelValue", false), 1200)
      return
    }
    if (progress.status === "waiting" || progress.status === "scanned") {
      schedule(current)
    }
  } catch (caught) {
    if (current !== generation) return
    status.value = "error"
    message.value = describe(caught)
  }
}

function close(): void {
  stop()
  emit("update:modelValue", false)
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) void begin()
    else stop()
  },
  { immediate: true },
)

onBeforeUnmount(stop)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="扫码登录韭研公社"
    width="360px"
    append-to-body
    @close="close"
  >
    <div class="jl-body">
      <div class="jl-qr" :class="{ dim: finished || status === 'scanned' }">
        <img v-if="qrImage" :src="qrImage" alt="微信登录二维码" width="220" height="220" />
        <span v-else class="jl-placeholder">{{ status === "error" ? "获取失败" : "加载中…" }}</span>
      </div>
      <p class="jl-msg" :class="{ ok: status === 'confirmed', bad: finished && status !== 'confirmed' }">
        {{ message }}
      </p>
      <p class="jl-tip">
        用微信「扫一扫」，在手机上点「允许」。登录会话由服务器加密保存，约两周后需要重新扫码。
      </p>
      <el-button v-if="finished" type="primary" @click="begin">重新获取二维码</el-button>
    </div>
  </el-dialog>
</template>

<style scoped>
.jl-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

.jl-qr {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 220px;
  height: 220px;
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}

.jl-qr.dim img {
  opacity: 0.25;
}

.jl-placeholder {
  color: var(--muted);
  font-size: 13px;
}

.jl-msg {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.jl-msg.ok {
  color: var(--ok, #2e9e5b);
}

.jl-msg.bad {
  color: var(--danger, #e5484d);
}

.jl-tip {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
  color: var(--muted);
}
</style>
