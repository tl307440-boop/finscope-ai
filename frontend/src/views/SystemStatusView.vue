<script setup lang="ts">
import { onMounted, ref } from "vue"
import InstrumentSearch from "../components/common/InstrumentSearch.vue"
import { ApiError } from "../api/client"
import {
  type SystemSnapshot,
  docsUrl,
  fetchSystemStatus,
  formatShanghai,
  reasonText,
} from "../api/health"

const phase = ref<"loading" | "ready" | "degraded" | "error">("loading")
const snapshot = ref<SystemSnapshot | null>(null)
const failure = ref("无法连接接口")
const failureRequestId = ref<string | null>(null)
let requestToken = 0

async function load(): Promise<void> {
  const current = ++requestToken
  phase.value = "loading"
  snapshot.value = null
  try {
    const next = await fetchSystemStatus()
    if (current !== requestToken) {
      return
    }
    snapshot.value = next
    phase.value = next.ready ? "ready" : "degraded"
  } catch (error) {
    if (current !== requestToken) {
      return
    }
    phase.value = "error"
    failure.value = error instanceof ApiError ? error.message : "无法连接接口"
    failureRequestId.value = error instanceof ApiError ? error.requestId : null
  }
}

function tone(check: { status: "up" | "down"; reason?: string }): string {
  if (check.reason === "heartbeat_stale") {
    return "stale"
  }
  return check.status === "up" ? "up" : "down"
}

function statusLabel(check: { status: "up" | "down"; reason?: string }): string {
  const reason = reasonText(check.reason)
  if (reason) {
    return reason
  }
  return check.status === "up" ? "就绪" : "不可用"
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="shell">
    <a class="skip-link" href="#main">跳到主要内容</a>
    <header class="topbar">
      <div class="brand">
        <strong>FinScope AI</strong>
        <span>金融投研辅助 · 不提供交易执行</span>
      </div>
      <div class="nav-links">
        <a href="/macro">美国宏观</a>
        <a href="/login">登录</a>
        <a :href="docsUrl()" target="_blank" rel="noopener noreferrer">打开接口文档</a>
      </div>
    </header>
    <main id="main" class="page">
      <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">
        正在检查运行状态
      </section>

      <section v-else-if="phase === 'error'" class="panel" role="alert">
        <h1>无法连接接口</h1>
        <p>{{ failure }}</p>
        <p v-if="failureRequestId" class="muted">请求编号 {{ failureRequestId }}</p>
        <button class="retry" type="button" @click="load">重试</button>
      </section>

      <template v-else-if="snapshot">
        <p v-if="snapshot.dataMode === 'demo'" class="banner">演示数据 / 非实时</p>
        <section class="panel" :role="snapshot.ready ? 'status' : 'alert'">
          <h1>{{ snapshot.ready ? "运行正常" : "部分组件不可用" }}</h1>
          <p class="muted">
            检查时间 {{ formatShanghai(snapshot.asOf) }}（北京时间） · 请求编号
            {{ snapshot.requestId }}
          </p>
          <p class="muted">
            {{
              snapshot.dataMode === "demo"
                ? snapshot.notice || "当前为演示模式"
                : "数据模式：live，未使用演示数据"
            }}
          </p>
          <button v-if="!snapshot.ready" class="retry" type="button" @click="load">重试</button>
        </section>
        <InstrumentSearch />
        <section class="status-grid">
          <article class="card">
            <h2>接口进程</h2>
            <p class="state up">就绪</p>
            <p class="muted">存活检查已返回</p>
          </article>
          <article class="card">
            <h2>数据库</h2>
            <p class="state" :class="tone(snapshot.database)">{{ statusLabel(snapshot.database) }}</p>
            <p class="muted">
              {{
                snapshot.database.latencyMs === undefined
                  ? "—"
                  : `延迟 ${snapshot.database.latencyMs} 毫秒`
              }}
            </p>
          </article>
          <article class="card">
            <h2>任务 Worker</h2>
            <p class="state" :class="tone(snapshot.worker)">{{ statusLabel(snapshot.worker) }}</p>
            <p class="muted">最近心跳 {{ formatShanghai(snapshot.worker.lastSeenAt) }}</p>
          </article>
          <article class="card">
            <h2>业务数据</h2>
            <p class="state">尚未接入</p>
            <p class="muted">行情和财报尚未接入。宏观页只展示序列定义，没有密钥时不填观测值。</p>
          </article>
        </section>
      </template>
    </main>
  </div>
</template>
