<script setup lang="ts">
import { ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { uploadStatusText } from "../api/uploads"
import { restoreSession, session } from "../stores/auth"

const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const message = ref("")
const file = ref<File | null>(null)

function choose(event: Event): void {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) {
    return
  }
  file.value = input.files?.[0] ?? null
}

async function submit(): Promise<void> {
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再上传"
    return
  }
  if (!file.value) {
    phase.value = "error"
    message.value = "请选择 PDF"
    return
  }
  phase.value = "loading"
  message.value = ""
  const body = new FormData()
  body.set("file", file.value)
  try {
    const response = await authorizedFetch(
      "/api/v1/documents",
      { method: "POST", body },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const payload = await readJson(response)
    const record = asRecord(payload)
    if (!response.ok) {
      const error = asRecord(record?.error)
      message.value = asString(error?.message) ?? "无法上传"
      phase.value = "error"
      return
    }
    const data = asRecord(record?.data)
    const status = asString(data?.status) ?? ""
    message.value = uploadStatusText(status, asString(data?.message) ?? "")
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

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
    <section class="panel">
      <h1>上传财报 PDF</h1>
      <p class="muted">只接受 PDF。超过大小或页数、内容不是 PDF、重复文件和扫描件都会给出状态。文件不会按原名保存。</p>
      <p v-if="!session.accessToken" class="muted">
        <a href="/login">请先登录</a>
      </p>
      <form class="form" @submit.prevent="submit">
        <label>
          PDF 文件
          <input name="pdf-file" type="file" accept="application/pdf,.pdf" @change="choose" />
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">上传</button>
        <p v-if="phase === 'loading'">正在校验</p>
        <p v-else-if="message" :role="phase === 'error' ? 'alert' : 'status'">{{ message }}</p>
      </form>
    </section>
  </main>
</template>
