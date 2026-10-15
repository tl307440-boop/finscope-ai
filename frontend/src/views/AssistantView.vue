<script setup lang="ts">
import { onMounted, ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { restoreSession, session } from "../stores/auth"

interface Turn {
  id: string
  role: string
  content: string
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const question = ref("")
const sessionId = ref("")
const turns = ref<Turn[]>([])
const sending = ref(false)

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再提问"
    return
  }
  try {
    const listed = await authorizedFetch(
      "/api/v1/assistant/sessions",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const listedBody = await readJson(listed)
    const sessions = asRecord(asRecord(listedBody)?.data)?.sessions
    const first = Array.isArray(sessions) ? asRecord(sessions[0]) : null
    const existing = asString(first?.id)
    if (existing) {
      sessionId.value = existing
    } else {
      const created = await authorizedFetch(
        "/api/v1/assistant/sessions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "研究问答" }),
        },
        session.accessToken,
        refreshToken,
      )
      const createdBody = await readJson(created)
      sessionId.value = asString(asRecord(asRecord(createdBody)?.data)?.id) ?? ""
    }
    await loadMessages()
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadMessages(): Promise<void> {
  if (!sessionId.value || !session.accessToken) {
    return
  }
  const response = await authorizedFetch(
    `/api/v1/assistant/sessions/${sessionId.value}/messages`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  const rows = asRecord(asRecord(payload)?.data)?.messages
  turns.value = Array.isArray(rows) ? rows.flatMap(readTurn) : []
}

async function ask(): Promise<void> {
  const text = question.value.trim()
  if (!text || !sessionId.value || !session.accessToken) {
    message.value = "请输入问题"
    return
  }
  sending.value = true
  message.value = ""
  const clientMessageId = crypto.randomUUID()
  try {
    const response = await authorizedFetch(
      `/api/v1/assistant/sessions/${sessionId.value}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_message_id: clientMessageId, content: text }),
      },
      session.accessToken,
      refreshToken,
    )
    if (!response.ok) {
      message.value = "无法发送"
      return
    }
    await response.text()
    question.value = ""
    await loadMessages()
  } catch {
    message.value = "无法连接接口"
  } finally {
    sending.value = false
  }
}

function readTurn(value: unknown): Turn[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  const role = asString(row?.role)
  const content = asString(row?.content)
  if (!id || !role || content === null) {
    return []
  }
  return [{ id, role, content }]
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

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取问答</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取问答</h1>
      <p>{{ message }}</p>
    </section>
    <section v-else class="panel">
      <h1>研究问答</h1>
      <p class="muted">未配置模型时不会编造财务数字。同一条问题中断后不会重新生成。</p>
      <ul v-if="turns.length">
        <li v-for="turn in turns" :key="turn.id">{{ turn.role === "user" ? "问" : "答" }}：{{ turn.content }}</li>
      </ul>
      <p v-else class="muted">还没有提问。</p>
      <form class="form" @submit.prevent="ask">
        <label>
          问题
          <input name="question" v-model="question" />
        </label>
        <button class="retry" type="submit" :disabled="sending">发送</button>
        <p v-if="message" role="status">{{ message }}</p>
      </form>
    </section>
  </main>
</template>
