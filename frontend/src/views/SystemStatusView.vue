<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { Connection, Cpu, DataBoard, DataLine, Document, Refresh, Search } from "@element-plus/icons-vue"
import InstrumentSearch from "../components/common/InstrumentSearch.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError } from "../api/client"
import {
  type ComponentCheck,
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

type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

/**
 * 错误态的正文。标题已经写了「无法连接接口」，正文就补上可操作的下一步，
 * 避免标题和正文重复同一句话。
 */
const failureDetail = computed(() => {
  const base =
    failure.value === "无法连接接口"
      ? "请确认后端进程已启动（默认 127.0.0.1:8000），然后重试。"
      : failure.value
  return failureRequestId.value ? `${base}（请求编号 ${failureRequestId.value}）` : base
})

function tone(check: ComponentCheck): Tone {
  if (check.reason === "heartbeat_stale") {
    return "warn"
  }
  return check.status === "up" ? "ok" : "danger"
}

function statusLabel(check: ComponentCheck): string {
  const reason = reasonText(check.reason)
  if (reason) {
    return reason
  }
  return check.status === "up" ? "就绪" : "不可用"
}

function databaseLatency(): string {
  const value = snapshot.value?.database.latencyMs
  return value === undefined ? "延迟未上报" : `延迟 ${value} 毫秒（就绪检查实测）`
}

function openDocs(): void {
  window.open(docsUrl(), "_blank", "noopener,noreferrer")
}

function workerHeartbeat(): string {
  return `最近心跳 ${formatShanghai(snapshot.value?.worker.lastSeenAt)}`
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main id="main" class="page">
    <PageHeader
      title="系统运行状态"
      subtitle="核对接口进程、数据库与后台 Worker 的实时就绪情况"
    >
      <template #actions>
        <el-button :loading="phase === 'loading'" :icon="Refresh" @click="load">重新检查</el-button>
        <el-button :icon="Document" @click="openDocs">接口文档</el-button>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        message="正在检查运行状态"
        :rows="4"
      />
      <StateBlock
        v-else-if="phase === 'error' || !snapshot"
        state="error"
        title="无法连接接口"
        :message="failureDetail"
        @retry="load"
      />
      <template v-else>
        <PanelCard title="就绪检查" info="GET /api/v1/health/live 与 /api/v1/health/ready">
          <template #icon><el-icon><Connection /></el-icon></template>
          <template #actions>
            <StatusPill
              :text="snapshot.ready ? '服务已就绪' : '部分组件不可用'"
              :tone="snapshot.ready ? 'ok' : 'danger'"
              dot
            />
          </template>

          <div class="ready">
            <h2 :class="snapshot.ready ? 'ok' : 'bad'">
              {{ snapshot.ready ? "运行正常" : "部分组件不可用" }}
            </h2>
            <p class="muted">
              检查时间 {{ formatShanghai(snapshot.asOf) }}（北京时间） · 请求编号 {{ snapshot.requestId }}
            </p>
            <p class="muted">
              {{
                snapshot.dataMode === "demo"
                  ? snapshot.notice || "当前为演示模式"
                  : "数据模式：live，未使用演示数据"
              }}
            </p>
            <el-button v-if="!snapshot.ready" size="small" @click="load">重试</el-button>
          </div>
        </PanelCard>

        <p v-if="snapshot.dataMode === 'demo'" class="banner">演示数据 / 非实时</p>

        <div class="grid-3">
          <StatCard
            title="接口进程"
            value="就绪"
            caption="GET /api/v1/health/live 已返回，进程存活检查不依赖数据库"
            info="存活检查与就绪检查来自同一个 FastAPI 进程"
            more-text=""
          >
            <template #icon><el-icon><Cpu /></el-icon></template>
            <template #below>
              <StatusPill text="进程存活" tone="ok" dot />
            </template>
          </StatCard>

          <StatCard
            title="数据库"
            :value="statusLabel(snapshot.database)"
            :caption="databaseLatency()"
            info="就绪检查里的 checks.database 分量"
            more-text=""
          >
            <template #icon><el-icon><DataLine /></el-icon></template>
            <template #below>
              <StatusPill
                :text="snapshot.database.status === 'up' ? '连接正常' : '连接失败'"
                :tone="tone(snapshot.database)"
                dot
              />
            </template>
          </StatCard>

          <StatCard
            title="任务 Worker"
            :value="statusLabel(snapshot.worker)"
            :caption="workerHeartbeat()"
            info="就绪检查里的 checks.worker 分量，心跳过期按心跳 TTL 判定"
            more-text=""
          >
            <template #icon><el-icon><DataBoard /></el-icon></template>
            <template #below>
              <StatusPill
                :text="snapshot.worker.status === 'up' ? '心跳正常' : '心跳异常'"
                :tone="tone(snapshot.worker)"
                dot
              />
            </template>
          </StatCard>
        </div>

        <div class="grid-2">
          <PanelCard title="业务数据覆盖" info="这里说明真实边界，不合成任何行情">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <dl class="kv">
              <dt>日线行情</dt>
              <dd>只覆盖已入库的少数指定 A 股，未同步时不展示价格。</dd>
              <dt>宏观观测</dt>
              <dd>未配置数据源密钥时不填观测值，页面显示「不可用」而不是旧值。</dd>
              <dt>接口现状</dt>
              <dd>后端没有提供数据覆盖率的汇总接口，因此本节只做定性说明，不给百分比。</dd>
            </dl>
          </PanelCard>

          <PanelCard title="标的检索" info="GET /api/v1/instruments/search">
            <template #icon><el-icon><Search /></el-icon></template>
            <InstrumentSearch />
            <p class="note">
              检索结果来自已入库的标的表；查不到只说明该标的尚未入库，不代表代码不存在。
            </p>
          </PanelCard>
        </div>

        <SourceFooter
          :as-of="formatShanghai(snapshot.asOf)"
          :sources="['/api/v1/health/live', '/api/v1/health/ready']"
          :stale="!snapshot.ready || snapshot.dataMode === 'demo'"
          note="本页数字全部来自上面两个健康检查接口，没有其它数据源。"
        />
      </template>
    </div>
  </main>
</template>

<style scoped>
.stack {
  margin-top: var(--gap);
}

.ready {
  display: grid;
  gap: 8px;
  justify-items: start;
}

.ready h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.ready h2.ok {
  color: var(--ok);
}

.ready h2.bad {
  color: var(--danger);
}

.ready p {
  margin: 0;
}

.kv {
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
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

.note {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
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
