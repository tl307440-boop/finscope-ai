<script setup lang="ts">
/**
 * A股市场温度（`GET /api/v1/market/temperature`）。
 *
 * 后端只做描述性统计：趋势 30% + 广度 35% + 活跃 20% + 波动 15%，
 * 样本不够时 status=insufficient、score=null、factors=[]，并给出 coverage 说明。
 * 这里把 factors 原样铺成因子卡：分值与权重都来自后端，缺分就写「无法判断」，
 * 不补 0、不插值、不造第二条曲线。板块状态沿用同一页的 `/market/sectors`。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString } from "../api/json"
import MetricTile from "../components/ui/MetricTile.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

interface FactorRow {
  code: string
  label: string
  weight: string
  score: number | null
  raw: string
  note: string
}

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const requestId = ref("")
const status = ref("")
const label = ref("")
const score = ref<number | null>(null)
const coverage = ref("")
const notice = ref("")
const factors = ref<FactorRow[]>([])
const sectorLines = ref<string[]>([])

const temperatureLabel = computed(() => label.value || "无法判断")

/** 后端 status 直接映射到状态样式，不自己发明「偏强/偏弱」的判断。 */
const tone = computed<"ok" | "warn" | "accent" | "muted">(() => {
  if (status.value === "ok") {
    return "ok"
  }
  if (status.value === "insufficient") {
    return "warn"
  }
  return "accent"
})

/** 0–100 的因子分，只用来画一根刻度尺；没有分数的因子里程为 0。 */
function scoreWidth(row: FactorRow): string {
  if (row.score === null) {
    return "0%"
  }
  return `${Math.max(0, Math.min(100, row.score))}%`
}

function scoreText(row: FactorRow): string {
  return row.score === null ? "无法判断" : row.score.toFixed(1)
}

/** 权重由后端以字符串给出（如 "0.30"），转成百分比展示，不做四舍五入以外的加工。 */
function weightText(row: FactorRow): string {
  const value = Number(row.weight)
  return Number.isFinite(value) ? `权重 ${(value * 100).toFixed(0)}%` : `权重 ${row.weight}`
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const response = await fetch(apiUrl("/api/v1/market/temperature"), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    const payload = await readJson(response)
    if (!response.ok) {
      throw errorFrom(payload, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(payload)?.data)
    requestId.value = asString(asRecord(asRecord(payload)?.meta)?.request_id) ?? ""
    status.value = asString(data?.status) ?? "unknown"
    label.value = asString(data?.label) ?? "无法判断"
    score.value = asNumber(data?.score)
    coverage.value = asString(data?.coverage) ?? ""
    notice.value = asString(data?.notice) ?? ""
    factors.value = Array.isArray(data?.factors)
      ? data.factors.flatMap((item) => {
          const record = asRecord(item)
          const code = asString(record?.code)
          const itemLabel = asString(record?.label)
          if (!record || !code || !itemLabel) {
            return []
          }
          return [
            {
              code,
              label: itemLabel,
              weight: asString(record.weight) ?? "—",
              score: asNumber(record.score),
              raw: asString(record.raw) ?? "",
              note: asString(record.note) ?? "",
            },
          ]
        })
      : []

    const sectorResponse = await fetch(apiUrl("/api/v1/market/sectors"), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    const sectorPayload = await readJson(sectorResponse)
    const rows = asRecord(asRecord(sectorPayload)?.data)?.sectors
    sectorLines.value = Array.isArray(rows)
      ? rows.flatMap((item) => {
          const row = asRecord(item)
          const name = asString(row?.name)
          const text = asString(row?.label)
          return name && text ? [`${name}：${text}`] : []
        })
      : []
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="市场温度"
      subtitle="趋势 + 广度 + 活跃 + 波动的描述性综合分，不代表收益预测"
    >
      <template #actions>
        <button class="retry" type="button" :disabled="phase === 'loading'" @click="load">重新计算</button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else>
      <div class="grid-1-2">
        <PanelCard title="综合温度" :info="`口径 ${coverage || '—'}`">
          <div class="hero">
            <p class="hero-score tabular">{{ score === null ? "—" : score.toFixed(1) }}</p>
            <div class="hero-side">
              <StatusPill :tone="tone" :text="temperatureLabel" />
              <p class="muted hero-note">0–100 分：≥60 偏强，≤40 偏弱，其余中性。</p>
            </div>
          </div>
          <p class="muted hero-coverage">样本口径：{{ coverage || "后端未给出覆盖度说明" }}</p>
          <p v-if="notice" class="muted hero-notice">{{ notice }}</p>
        </PanelCard>

        <PanelCard title="覆盖与说明" info="score 为 null 时后端不给分，这里照实写明原因">
          <dl class="facts">
            <div>
              <dt>状态</dt>
              <dd>{{ status || "—" }}</dd>
            </div>
            <div>
              <dt>结论</dt>
              <dd>{{ temperatureLabel }}</dd>
            </div>
            <div>
              <dt>综合分</dt>
              <dd class="tabular">{{ score === null ? "后端未给分" : score.toFixed(1) }}</dd>
            </div>
            <div>
              <dt>因子数</dt>
              <dd class="tabular">{{ factors.length }}</dd>
            </div>
          </dl>
          <p class="muted">
            该页只读 `/api/v1/market/temperature`；不与指数行情、板块强度互相推算。
          </p>
        </PanelCard>
      </div>

      <PanelCard title="因子明细" info="权重与分值均由后端给出，缺分写「无法判断」，不补 0">
        <div v-if="!factors.length" class="factor-empty">
          <StatusPill tone="warn" :text="temperatureLabel" />
          <p class="muted">
            {{ coverage || "后端没有返回可用的因子样本。" }}
            {{ notice }}
          </p>
          <p class="muted">样本补齐后这里会列出每个因子的分值与原始读数。</p>
        </div>
        <div v-else class="factor-grid">
          <div v-for="row in factors" :key="row.code" class="factor">
            <MetricTile :label="row.label" :value="scoreText(row)" :hint="weightText(row)" />
            <span class="track" role="img" :aria-label="`${row.label} 分值 ${scoreText(row)}`">
              <span class="fill" :style="{ width: scoreWidth(row) }" />
            </span>
            <p class="factor-raw">{{ row.raw || "后端未给出原始读数" }}</p>
            <p class="muted factor-note">{{ row.note }}</p>
          </div>
        </div>
      </PanelCard>

      <div class="grid-2">
        <PanelCard title="板块状态" info="同页读取 /api/v1/market/sectors 的标签，不另算强度">
          <p v-if="!sectorLines.length" class="muted">还没有可计算的行业成分。</p>
          <ul v-else class="sector-list">
            <li v-for="item in sectorLines" :key="item">{{ item }}</li>
          </ul>
        </PanelCard>

        <PanelCard title="口径提醒" info="后端 notice 原文">
          <p class="muted">
            {{ notice || "后端未提供额外说明。" }}
          </p>
          <p class="muted">
            温度分只描述当前样本的相对位置：波动处于高位会被当作风险扣分，而不是「更有机会」。
          </p>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="coverage || '—'"
        :sources="['指数与成分日线（东方财富 / 腾讯行情，已入库）', 'market-temperature-v1']"
        :note="notice || '描述性指标，不是收益预测；样本不足时后端直接返回无法判断'"
      />
    </template>
  </main>
</template>

<style scoped>
.hero {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
}

.hero-score {
  margin: 0;
  font-size: 44px;
  font-weight: 700;
  line-height: 1;
}

.hero-side {
  display: grid;
  gap: 6px;
}

.hero-note {
  margin: 0;
  font-size: 12px;
}

.hero-coverage,
.hero-notice {
  margin: 4px 0 0;
  font-size: 12px;
}

.facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin: 0 0 12px;
}

.facts dt {
  font-size: 12px;
  color: var(--muted);
}

.facts dd {
  margin: 2px 0 0;
  font-size: 15px;
  font-weight: 600;
}

.factor-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--gap);
}

.factor-empty {
  display: grid;
  gap: 8px;
  justify-items: start;
}

.factor {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel-soft);
}

.track {
  display: block;
  height: 8px;
  margin: 10px 0 8px;
  border-radius: 999px;
  background: var(--line);
  overflow: hidden;
}

.fill {
  display: block;
  height: 100%;
  background: var(--accent);
}

.factor-raw {
  margin: 0;
  font-size: 12px;
}

.factor-note {
  margin: 4px 0 0;
  font-size: 12px;
}

.sector-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
}
</style>
