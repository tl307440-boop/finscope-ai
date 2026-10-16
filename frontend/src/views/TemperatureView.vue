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
import { Odometer, PieChart, Grid, Reading } from "@element-plus/icons-vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, formatMoment } from "../api/json"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"

interface FactorRow {
  code: string
  label: string
  weight: number | null
  score: number | null
  raw: string
  note: string
}

interface SectorLine {
  name: string
  label: string
  score: number
}

/** 因子配色只做区分，不表达涨跌。 */
const FACTOR_COLORS = ["#22d3ee", "#3b82f6", "#a78bfa", "#fb923c", "#34d399"]

/** 半圆仪表的几何参数（viewBox 260×170）。 */
const GAUGE = { cx: 130, cy: 140, r: 108 }
const RING = { size: 92, r: 38, stroke: 7 }

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const status = ref("")
const label = ref("")
const score = ref<number | null>(null)
const coverage = ref("")
const notice = ref("")
const asOf = ref<string | null>(null)
const stale = ref(false)
const factors = ref<FactorRow[]>([])
const sectorLines = ref<SectorLine[]>([])
const unscoredSectors = ref(0)

const temperatureLabel = computed(() => label.value || "无法判断")
const insufficient = computed(() => status.value === "insufficient" || score.value === null)

/** 温度不是涨跌：偏冷用青色、中性用蓝、偏热用橙。 */
const zoneClass = computed(() => {
  if (score.value === null) {
    return "zone-none"
  }
  if (score.value >= 60) {
    return "zone-hot"
  }
  if (score.value <= 40) {
    return "zone-cool"
  }
  return "zone-mid"
})

function clamp(value: number): number {
  return Math.max(0, Math.min(100, value))
}

function gaugePoint(value: number, radius = GAUGE.r): { x: number; y: number } {
  const theta = Math.PI * (1 - clamp(value) / 100)
  return { x: GAUGE.cx + radius * Math.cos(theta), y: GAUGE.cy - radius * Math.sin(theta) }
}

function arcPath(from: number, to: number, radius = GAUGE.r): string {
  const start = gaugePoint(from, radius)
  const end = gaugePoint(to, radius)
  return `M ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${radius} ${radius} 0 0 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)}`
}

const ticks = computed(() =>
  [0, 20, 40, 60, 80, 100].map((value) => ({
    value,
    inner: gaugePoint(value, GAUGE.r - 16),
    outer: gaugePoint(value, GAUGE.r - 10),
    text: gaugePoint(value, GAUGE.r - 28),
  })),
)

const needle = computed(() => (score.value === null ? null : gaugePoint(score.value)))

const ringCircumference = 2 * Math.PI * RING.r

function ringDash(value: number | null): string {
  const length = value === null ? 0 : (clamp(value) / 100) * ringCircumference
  return `${length.toFixed(2)} ${ringCircumference.toFixed(2)}`
}

function factorColor(index: number): string {
  return FACTOR_COLORS[index % FACTOR_COLORS.length] ?? "#3b82f6"
}

function scoreText(value: number | null, digits = 1): string {
  return value === null ? "—" : value.toFixed(digits)
}

function weightText(row: FactorRow): string {
  return row.weight === null ? "—" : `${Math.round(row.weight * 100)}%`
}

/** 原始读数里的长小数只保留两位，文字原样保留。 */
function tidyRaw(text: string): string {
  return text.replace(/-?\d+\.\d{3,}/g, (match) => Number(match).toFixed(2))
}

/** 加权贡献 = 权重 × 因子分，四段相加即综合分；任一项缺失就不画这段。 */
const contributions = computed(() =>
  factors.value.flatMap((row, index) =>
    row.weight !== null && row.score !== null
      ? [{ code: row.code, label: row.label, value: row.weight * row.score, color: factorColor(index) }]
      : [],
  ),
)

const weightSummary = computed(() => factors.value.map((row) => `${row.label} ${weightText(row)}`).join(" · "))

async function loadSectors(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/market/sectors"), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    if (!response.ok) {
      sectorLines.value = []
      unscoredSectors.value = 0
      return
    }
    const payload = await readJson(response)
    const rows = asRecord(asRecord(payload)?.data)?.sectors
    const list = Array.isArray(rows) ? rows : []
    const scored: SectorLine[] = []
    let missing = 0
    for (const item of list) {
      const row = asRecord(item)
      const name = asString(row?.name)
      const value = asNumber(row?.score)
      if (!name) {
        continue
      }
      if (value === null) {
        missing += 1
      } else {
        scored.push({ name, label: asString(row?.label) ?? "", score: value })
      }
    }
    sectorLines.value = scored.sort((a, b) => b.score - a.score)
    unscoredSectors.value = missing
  } catch {
    sectorLines.value = []
    unscoredSectors.value = 0
  }
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
    const meta = asRecord(asRecord(payload)?.meta)
    asOf.value = asString(meta?.as_of)
    stale.value = meta?.stale === true
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
              weight: asNumber(record.weight),
              score: asNumber(record.score),
              raw: asString(record.raw) ?? "",
              note: asString(record.note) ?? "",
            },
          ]
        })
      : []
    await loadSectors()
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接行情服务"
    phase.value = "error"
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader title="市场温度" subtitle="趋势、广度、活跃、波动的描述性综合分，不代表收益预测">
      <template #actions>
        <el-button :loading="phase === 'loading'" @click="load">重新计算</el-button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />

    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else>
      <StateBlock
        v-if="stale"
        state="stale"
        message="温度数据可能已过期，展示的是最近一次成功计算的结果"
        :as-of="formatMoment(asOf)"
        @retry="load"
      />

      <div class="grid-1-2">
        <PanelCard title="综合温度" :info="coverage ? `样本：${coverage}` : ''" tone="cyan">
          <template #icon><el-icon><Odometer /></el-icon></template>
          <div class="gauge-wrap" :class="zoneClass">
            <svg class="gauge" viewBox="0 0 260 170" role="img" :aria-label="`综合温度 ${scoreText(score)}，${temperatureLabel}`">
              <defs>
                <linearGradient id="temp-gauge-grad" x1="22" y1="0" x2="238" y2="0" gradientUnits="userSpaceOnUse">
                  <stop offset="0%" stop-color="#22d3ee" />
                  <stop offset="50%" stop-color="#3b82f6" />
                  <stop offset="100%" stop-color="#fb923c" />
                </linearGradient>
                <filter id="temp-gauge-glow" x="-50%" y="-50%" width="200%" height="200%">
                  <feGaussianBlur stdDeviation="3" />
                </filter>
              </defs>
              <path :d="arcPath(0, 100)" class="gauge-track" />
              <path :d="arcPath(0, 40, GAUGE.r + 9)" class="gauge-zone zone-c" />
              <path :d="arcPath(40, 60, GAUGE.r + 9)" class="gauge-zone zone-m" />
              <path :d="arcPath(60, 100, GAUGE.r + 9)" class="gauge-zone zone-h" />
              <template v-if="score !== null">
                <path :d="arcPath(0, score)" class="gauge-glow" filter="url(#temp-gauge-glow)" />
                <path :d="arcPath(0, score)" class="gauge-value" />
              </template>
              <g v-for="tick in ticks" :key="tick.value">
                <line :x1="tick.inner.x" :y1="tick.inner.y" :x2="tick.outer.x" :y2="tick.outer.y" class="gauge-tick" />
                <text :x="tick.text.x" :y="tick.text.y + 4" text-anchor="middle" class="gauge-tick-text">{{ tick.value }}</text>
              </g>
              <circle v-if="needle" :cx="needle.x" :cy="needle.y" r="7" class="gauge-dot" />
            </svg>
            <div class="gauge-readout">
              <p class="gauge-score tabular">{{ scoreText(score) }}</p>
              <p class="gauge-label">{{ temperatureLabel }}</p>
            </div>
          </div>
          <ul class="zone-legend">
            <li><i class="dot zone-c" />≤ 40 偏弱</li>
            <li><i class="dot zone-m" />40–60 中性</li>
            <li><i class="dot zone-h" />≥ 60 偏强</li>
          </ul>
          <p class="hero-coverage">
            <template v-if="insufficient">{{ coverage || "样本不足，暂不计算温度" }}</template>
            <template v-else>样本：{{ coverage || "—" }}</template>
          </p>
        </PanelCard>

        <PanelCard title="因子构成" :info="weightSummary ? `权重：${weightSummary}` : ''" tone="violet">
          <template #icon><el-icon><PieChart /></el-icon></template>
          <StateBlock
            v-if="!factors.length"
            state="empty"
            :message="coverage ? `${coverage}，暂不计算各因子` : '样本不足，暂不计算各因子'"
          />
          <template v-else>
            <div class="factor-grid">
              <article v-for="(row, index) in factors" :key="row.code" class="factor">
                <div class="ring-wrap">
                  <svg :width="RING.size" :height="RING.size" :viewBox="`0 0 ${RING.size} ${RING.size}`" aria-hidden="true">
                    <circle :cx="RING.size / 2" :cy="RING.size / 2" :r="RING.r" class="ring-track" :stroke-width="RING.stroke" />
                    <circle
                      :cx="RING.size / 2"
                      :cy="RING.size / 2"
                      :r="RING.r"
                      fill="none"
                      :stroke="factorColor(index)"
                      :style="{ color: factorColor(index) }"
                      :stroke-width="RING.stroke"
                      stroke-linecap="round"
                      :stroke-dasharray="ringDash(row.score)"
                      :transform="`rotate(-90 ${RING.size / 2} ${RING.size / 2})`"
                      class="ring-value"
                    />
                  </svg>
                  <span class="ring-score tabular">{{ scoreText(row.score) }}</span>
                </div>
                <div class="factor-body">
                  <p class="factor-head">
                    <span class="factor-name" :style="{ color: factorColor(index) }">{{ row.label }}</span>
                    <span class="factor-weight">权重 {{ weightText(row) }}</span>
                  </p>
                  <p class="factor-note">{{ row.note || "—" }}</p>
                  <p class="factor-raw tabular">{{ row.raw ? tidyRaw(row.raw) : "暂无原始读数" }}</p>
                </div>
              </article>
            </div>

            <div v-if="contributions.length" class="contrib">
              <p class="contrib-head">
                <span>加权贡献</span>
                <span class="muted">各因子分 × 权重，合计即综合分</span>
              </p>
              <div class="contrib-bar" role="img" :aria-label="`加权合计 ${scoreText(score)}`">
                <span
                  v-for="item in contributions"
                  :key="item.code"
                  class="contrib-seg"
                  :style="{ width: `${item.value}%`, background: item.color }"
                  :title="`${item.label} ${item.value.toFixed(1)}`"
                />
              </div>
              <ul class="contrib-legend">
                <li v-for="item in contributions" :key="item.code">
                  <i :style="{ background: item.color }" />
                  {{ item.label }}
                  <strong class="tabular">{{ item.value.toFixed(1) }}</strong>
                </li>
                <li class="contrib-total">
                  合计 <strong class="tabular">{{ scoreText(score) }}</strong>
                </li>
              </ul>
            </div>
          </template>
        </PanelCard>
      </div>

      <div class="grid-2 section">
        <PanelCard title="板块温度" info="与板块排行同一口径；样本不足的板块不评分" more-text="板块排行" more-to="/market/sectors">
          <template #icon><el-icon><Grid /></el-icon></template>
          <p v-if="!sectorLines.length" class="muted">暂无可评分的行业板块。</p>
          <ul v-else class="sector-list">
            <li v-for="item in sectorLines" :key="item.name">
              <span class="sector-name">{{ item.name }}</span>
              <span class="sector-bar"><i :style="{ width: `${clamp(item.score)}%` }" /></span>
              <span class="sector-score tabular">{{ item.score.toFixed(1) }}</span>
              <span class="sector-label">{{ item.label }}</span>
            </li>
          </ul>
          <p v-if="unscoredSectors" class="muted sector-foot">另有 {{ unscoredSectors }} 个板块成分股样本不足，暂不评分。</p>
        </PanelCard>

        <PanelCard title="口径说明" tone="orange">
          <template #icon><el-icon><Reading /></el-icon></template>
          <ul class="method-list">
            <li v-if="weightSummary">综合分由 {{ weightSummary }} 加权得到，0–100 分。</li>
            <li>波动处于高位会被当作风险扣分，而不是「更有机会」。</li>
            <li>只统计已入库日线的样本，未同步的股票不参与计算。</li>
            <li v-if="notice">{{ notice }}</li>
          </ul>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="formatMoment(asOf)"
        :sources="['指数与成分股日线（已入库）']"
        :stale="stale"
        note="描述性指标，不是收益预测"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.gauge-wrap {
  position: relative;
  max-width: 380px;
  margin: 4px auto 0;
}

.gauge {
  display: block;
  width: 100%;
  height: auto;
}

.gauge-track {
  fill: none;
  stroke: rgb(96 140 220 / 12%);
  stroke-width: 14;
  stroke-linecap: round;
}

.gauge-zone {
  fill: none;
  stroke-width: 2;
  opacity: 0.75;
}

.zone-c {
  stroke: #22d3ee;
  background: #22d3ee;
}

.zone-m {
  stroke: #3b82f6;
  background: #3b82f6;
}

.zone-h {
  stroke: #fb923c;
  background: #fb923c;
}

.gauge-value,
.gauge-glow {
  fill: none;
  stroke: url(#temp-gauge-grad);
  stroke-width: 14;
  stroke-linecap: round;
}

.gauge-glow {
  opacity: 0.55;
}

.gauge-tick {
  stroke: rgb(148 164 190 / 55%);
  stroke-width: 1.5;
}

.gauge-tick-text {
  fill: var(--muted);
  font-size: 11px;
  font-family: var(--font-num);
}

.gauge-dot {
  fill: var(--text-strong);
  stroke: #3b82f6;
  stroke-width: 3;
  filter: drop-shadow(0 0 6px rgb(59 130 246 / 80%));
}

.gauge-readout {
  position: absolute;
  left: 0;
  right: 0;
  top: 47%;
  text-align: center;
  pointer-events: none;
}

.gauge-score {
  margin: 0;
  font-family: var(--font-num);
  font-size: 48px;
  font-weight: 700;
  line-height: 1;
  color: var(--text-strong);
  text-shadow: 0 0 18px rgb(59 130 246 / 45%);
}

.gauge-label {
  display: inline-block;
  margin: 6px 0 0;
  padding: 1px 10px;
  border-radius: 999px;
  font-size: 13px;
  color: var(--text);
  background: rgb(96 140 220 / 14%);
}

.zone-cool .gauge-label {
  color: #22d3ee;
  background: rgb(34 211 238 / 14%);
}

.zone-hot .gauge-label {
  color: #fb923c;
  background: rgb(251 146 60 / 14%);
}

.zone-none .gauge-score {
  color: var(--faint);
  text-shadow: none;
}

.zone-legend {
  display: flex;
  justify-content: center;
  gap: 18px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
  color: var(--muted);
}

.zone-legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.hero-coverage {
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
  text-align: center;
  font-size: 13px;
  color: var(--muted);
}

.factor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.factor {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 6px 4px;
  min-width: 0;
}

.ring-wrap {
  position: relative;
  flex: none;
}

.ring-wrap svg {
  display: block;
}

.ring-track {
  fill: none;
  stroke: rgb(96 140 220 / 14%);
}

.ring-value {
  filter: drop-shadow(0 0 4px currentcolor);
}

.ring-score {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-strong);
}

.factor-body {
  min-width: 0;
  display: grid;
  gap: 4px;
}

.factor-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 0;
}

.factor-name {
  font-size: 16px;
  font-weight: 600;
}

.factor-weight {
  font-size: 13px;
  color: var(--muted);
}

.factor-note {
  margin: 0;
  font-size: 13px;
  color: var(--text);
}

.factor-raw {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--muted);
}

.contrib {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}

.contrib-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}

.contrib-head .muted {
  font-weight: 400;
}

.contrib-bar {
  display: flex;
  height: 12px;
  border-radius: 999px;
  background: rgb(96 140 220 / 10%);
  overflow: hidden;
}

.contrib-seg {
  display: block;
  height: 100%;
  box-shadow: -1px 0 0 var(--panel) inset;
}

.contrib-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
  font-size: 13px;
  color: var(--muted);
}

.contrib-legend li {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.contrib-legend i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.contrib-legend strong {
  color: var(--text-strong);
  font-weight: 600;
}

.contrib-total {
  margin-left: auto;
}

.sector-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}

.sector-list li {
  display: grid;
  grid-template-columns: 80px minmax(0, 1fr) 44px 40px;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}

.sector-name {
  color: var(--text-strong);
  font-weight: 500;
}

.sector-bar {
  display: block;
  height: 8px;
  border-radius: 999px;
  background: rgb(96 140 220 / 10%);
  overflow: hidden;
}

.sector-bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #22d3ee, #3b82f6);
}

.sector-score {
  text-align: right;
  font-weight: 600;
}

.sector-label {
  font-size: 13px;
  color: var(--muted);
}

.sector-foot {
  margin: 12px 0 0;
}

.method-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

@media (max-width: 1400px) {
  .factor-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
