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

const UNREACHABLE = "无法连接服务"

const phase = ref<"loading" | "ready" | "degraded" | "error">("loading")
const snapshot = ref<SystemSnapshot | null>(null)
const failure = ref(UNREACHABLE)
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
    failure.value = error instanceof ApiError ? error.message : UNREACHABLE
    failureRequestId.value = error instanceof ApiError ? error.requestId : null
  }
}

type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

/**
 * 错误态的正文。标题已经写了「无法连接服务」，正文就补上可操作的下一步，
 * 避免标题和正文重复同一句话。
 */
const failureDetail = computed(() => {
  const base =
    failure.value === UNREACHABLE
      ? "服务暂时没有响应，请稍后重试；持续失败请联系管理员。"
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
  return value === undefined ? "本次检查没有测到响应延迟" : `延迟 ${value} 毫秒（就绪检查实测）`
}

function openDocs(): void {
  window.open(docsUrl(), "_blank", "noopener,noreferrer")
}

function workerHeartbeat(): string {
  const seen = snapshot.value?.worker.lastSeenAt
  return seen ? `最近心跳 ${formatShanghai(seen)}` : "还没有收到心跳"
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main id="main" class="page">
    <PageHeader
      title="系统运行状态"
      subtitle="核对服务进程、数据库与后台任务的实时就绪情况"
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
        :title="UNREACHABLE"
        :message="failureDetail"
        @retry="load"
      />
      <template v-else>
        <PanelCard title="就绪检查" info="同时检查服务进程、数据库连接与后台任务心跳">
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
            <p class="muted" :title="`请求编号 ${snapshot.requestId}`">
              检查时间 {{ formatShanghai(snapshot.asOf) }}（北京时间）
            </p>
            <p class="muted">
              {{
                snapshot.dataMode === "demo"
                  ? snapshot.notice || "当前为演示模式"
                  : "实时数据模式，未使用演示数据"
              }}
            </p>
            <el-button v-if="!snapshot.ready" size="small" @click="load">重试</el-button>
          </div>
        </PanelCard>

        <p v-if="snapshot.dataMode === 'demo'" class="banner">演示数据 / 非实时</p>

        <div class="grid-3">
          <StatCard
            title="服务进程"
            value="就绪"
            caption="服务进程在线，存活检查不依赖数据库"
            tone="accent"
          >
            <template #icon><el-icon><Cpu /></el-icon></template>
            <template #below>
              <StatusPill class="below-pill" text="进程存活" tone="ok" dot />
            </template>
          </StatCard>

          <StatCard
            title="数据库"
            :value="statusLabel(snapshot.database)"
            :caption="databaseLatency()"
            info="数据库连接是否可用，以及本次检查的响应延迟"
            tone="cyan"
          >
            <template #icon><el-icon><DataLine /></el-icon></template>
            <template #below>
              <StatusPill
                class="below-pill"
                :text="snapshot.database.status === 'up' ? '连接正常' : '连接失败'"
                :tone="tone(snapshot.database)"
                dot
              />
            </template>
          </StatCard>

          <StatCard
            title="后台任务"
            :value="statusLabel(snapshot.worker)"
            :caption="workerHeartbeat()"
            info="负责执行数据同步等后台任务；长时间没有心跳会判为异常"
            tone="violet"
          >
            <template #icon><el-icon><DataBoard /></el-icon></template>
            <template #below>
              <StatusPill
                class="below-pill"
                :text="snapshot.worker.status === 'up' ? '心跳正常' : '心跳异常'"
                :tone="tone(snapshot.worker)"
                dot
              />
            </template>
          </StatCard>
        </div>

        <div class="grid-2">
          <PanelCard title="业务数据覆盖" info="各类数据的收录范围；没有收录的内容不会用估算值代替">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <dl class="kv">
              <dt>日线行情</dt>
              <dd>覆盖已收录的 A 股与指数，未同步的标的不展示价格。</dd>
              <dt>美国宏观</dt>
              <dd>来自 FRED（美联储圣路易斯分行）官方发布的数据。</dd>
              <dt>中国宏观</dt>
              <dd>需管理员在数据中心导入国家统计局等官方 CSV，导入前显示为不可用。</dd>
              <dt>美股财报</dt>
              <dd>来自 SEC EDGAR（美国证监会电子披露系统）的公司申报数据。</dd>
            </dl>
          </PanelCard>

          <PanelCard title="标的检索" info="检索已收录的证券">
            <template #icon><el-icon><Search /></el-icon></template>
            <InstrumentSearch />
            <p class="note">查不到只说明该标的尚未收录，不代表代码不存在。</p>
          </PanelCard>
        </div>

        <SourceFooter
          :as-of="formatShanghai(snapshot.asOf)"
          :sources="['服务健康检查']"
          :stale="!snapshot.ready || snapshot.dataMode === 'demo'"
          note="本页状态全部来自服务自身的健康检查"
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

.below-pill {
  margin-top: 8px;
}

.note {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 13px;
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
