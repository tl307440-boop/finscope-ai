<script setup lang="ts">
/**
 * 宏观综合判断（/macro/scenarios，效果图 2 的「宏观综合判断」页签内容）。
 *
 * 真实接口（与 backend/app/schemas/macro.py 的 AssessmentData、
 * backend/app/services/macro_rules.py 一致）：
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value,
 *               reason, evidence[] }, notice, version }
 *      · 取不到两个已公布观测值时 value 缺省、reason="insufficient_data"
 *      · 打分规则 macro-rules-v1：比较该序列最近两个已公布观测值，
 *        上行→增长/通胀记 70、流动性（联邦基金利率，tighter_when_rising）记 30；
 *        下行→70/30 互换；持平→50
 *      · regime_code = "<增长>-<通胀>-<流动性>"，任一维度取不到值时为 "insufficient_data"
 *  - GET /api/v1/macro/series：只取 availability，用来说明公开 CSV 不能回看历史观察日
 *
 * 只读边界：后端没有 POST /api/v1/macro/assessments，本页不提供情景输入表单；
 * 情景只描述「下一期方向如何改变哪一维」，不生成概率或前端自造结论。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, scaleAmount } from "../api/json"
import { macroSourceLabel, parseEvidence, regimeText } from "../api/macro"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import RegimeHistoryPanel from "../components/ui/RegimeHistoryPanel.vue"

type Phase = "loading" | "ready" | "error"
type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

interface DimensionScore {
  code: string
  value: number | null
  reason: string | null
  evidence: string[]
}

interface Assessment {
  pitDate: string
  regimeCode: string
  growth: DimensionScore | null
  inflation: DimensionScore | null
  liquidity: DimensionScore | null
  notice: string
}

/** 规则口径引用的序列；与 backend/app/services/macro_rules.py 一致。 */
interface RuleSeries {
  code: string
  name: string
  dimension: string
  rule: string
}

const RULE_SERIES: RuleSeries[] = [
  { code: "GDP", name: "美国国内生产总值", dimension: "增长", rule: "上行记 70，下行记 30，持平记 50" },
  { code: "CPIAUCSL", name: "美国消费者物价指数", dimension: "通胀", rule: "上行记 70，下行记 30，持平记 50" },
  {
    code: "FEDFUNDS",
    name: "联邦基金有效利率",
    dimension: "流动性",
    rule: "上行记 30（偏紧），下行记 70（偏松），持平记 50",
  },
]

const DIMENSION_SERIES: Record<string, RuleSeries | undefined> = {
  growth: RULE_SERIES[0],
  inflation: RULE_SERIES[1],
  liquidity: RULE_SERIES[2],
}

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const phase = ref<Phase>("loading")
/** 单独的请求中标志：phase 在模板里已被 v-if 收窄，不能再拿它给按钮做 loading。 */
const busy = ref(false)
const failure = ref("")
const assessment = ref<Assessment | null>(null)
const availability = ref("")
const asOf = ref("")
const appliedAsOf = ref("")

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`

const sourceLabel = computed<string>(() => macroSourceLabel(availability.value))

const pastDateUnsupported = computed<boolean>(
  () => availability.value === "public_csv" && Boolean(appliedAsOf.value) && appliedAsOf.value < browserToday,
)

const dimensions = computed<DimensionScore[]>(() => {
  const current = assessment.value
  if (!current) {
    return []
  }
  return [current.growth, current.inflation, current.liquidity].flatMap((item) => (item ? [item] : []))
})

const allInsufficient = computed<boolean>(
  () => dimensions.value.length > 0 && dimensions.value.every((item) => item.value === null),
)

const regimeLabel = computed<string>(() => regimeText(assessment.value?.regimeCode ?? "") || "—")

/** 指标卡里放不下完整中文，按「增长 / 通胀 / 流动性」顺序只留方向词。 */
const regimeShort = computed<string>(() =>
  dimensions.value.map((score) => scorePill(score).text).join(" / ") || "—",
)

const regimePill = computed<{ text: string; tone: Tone }>(() => {
  const current = assessment.value
  if (!current || !current.regimeCode) {
    return { text: "未取到", tone: "muted" }
  }
  if (current.regimeCode === "insufficient_data" || allInsufficient.value) {
    return { text: "数据不足", tone: "muted" }
  }
  return { text: "规则判断", tone: "accent" }
})

const latestPublished = computed<string>(() => {
  const dates = dimensions.value.flatMap((row) => row.evidence.map((line) => parseEvidence(line).published))
  return dates.filter(Boolean).sort().pop() ?? ""
})

function dimensionLabel(code: string): string {
  return code === "growth" ? "增长" : code === "inflation" ? "通胀" : code === "liquidity" ? "流动性" : code
}

/** 情景切换条件：只按规则写「下一期往哪个方向走会改变哪一维」。 */
const scenarioRows = computed<{ key: string; label: string; tone: Tone; title: string; basis: string }[]>(() => {
  if (!assessment.value || allInsufficient.value) {
    return []
  }
  const rows: { key: string; label: string; tone: Tone; title: string; basis: string }[] = [
    {
      key: "base",
      label: "基准",
      tone: "accent",
      title: `延续「${regimeLabel.value}」`,
      basis: "三条序列下一期公布值的方向与本期相同，组合状态不变。",
    },
  ]
  for (const score of dimensions.value) {
    if (score.value === null) {
      continue
    }
    const series = DIMENSION_SERIES[score.code]
    const name = series?.name ?? score.code
    if (score.code === "liquidity") {
      const tight = score.value <= 30
      rows.push({
        key: score.code,
        label: tight ? "流动性转松" : "流动性转紧",
        tone: "warn",
        title: tight ? "流动性由偏紧转为偏松" : "流动性由偏松转为偏紧",
        basis: tight ? `${name}下一期低于本期（降息）。` : `${name}下一期高于本期（加息）。`,
      })
      continue
    }
    const dim = dimensionLabel(score.code)
    const rising = score.value >= 50
    rows.push({
      key: score.code,
      label: rising ? `${dim}转弱` : `${dim}回升`,
      tone: "muted",
      title: rising ? `${dim}由上行转为下行` : `${dim}由下行转为上行`,
      basis: rising ? `${name}下一期公布值低于本期。` : `${name}下一期公布值高于本期。`,
    })
  }
  return rows
})

/** 证据抽屉：哪一个维度展开在右侧，默认打开第一个拿到依据的维度。 */
const activeDimension = ref<string>("")

const evidenceRows = computed<{ code: string; label: string; score: DimensionScore | null }[]>(() =>
  ["growth", "inflation", "liquidity"].map((code) => ({
    code,
    label: dimensionLabel(code),
    score:
      code === "growth"
        ? (assessment.value?.growth ?? null)
        : code === "inflation"
          ? (assessment.value?.inflation ?? null)
          : (assessment.value?.liquidity ?? null),
  })),
)

const activeEvidence = computed(() => {
  const found = evidenceRows.value.find((row) => row.code === activeDimension.value)
  return found ?? evidenceRows.value[0] ?? null
})

/**
 * 增长/通胀 70=上行、30=下行；流动性 30=利率上行＝偏紧、70=利率下行＝偏松。
 * 宏观维度不是涨跌，配色用中性色。
 */
function scorePill(score: DimensionScore | null): { text: string; tone: Tone } {
  if (!score || score.value === null) {
    return { text: "数据不足", tone: "muted" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value >= 70) {
    return { text: liquidity ? "偏松" : "上行", tone: "accent" }
  }
  if (score.value <= 30) {
    return { text: liquidity ? "偏紧" : "下行", tone: "warn" }
  }
  return { text: "持平", tone: "muted" }
}

function evidenceLine(code: string, line: string): { period: string; value: string; published: string } {
  const point = parseEvidence(line)
  if (!point.date) {
    return { period: line, value: "", published: "" }
  }
  let value = point.value === null ? "" : point.value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
  if (point.value !== null && code === "GDP") {
    const scaled = scaleAmount(point.value * 1e9)
    value = `${scaled.text} ${scaled.scale}美元`
  } else if (point.value !== null && code === "FEDFUNDS") {
    value = `${point.value.toFixed(2)}%`
  }
  let period = point.date.slice(0, 7)
  if (code === "GDP") {
    const month = Number(point.date.slice(5, 7))
    period = `${point.date.slice(0, 4)} Q${Math.floor((month - 1) / 3) + 1}`
  }
  return { period, value, published: point.published }
}

const insufficientText = computed<string>(() =>
  pastDateUnsupported.value
    ? "当前数据来自 FRED 公开 CSV，只有最新修订值，无法回看所选观察日当时已公布的数据。请回到今天。"
    : "所选观察日每个维度的已公布数据都不足两期，暂不判断。",
)

function parseScore(value: unknown): DimensionScore | null {
  const record = asRecord(value)
  const code = asString(record?.code)
  if (!code) {
    return null
  }
  const evidence = Array.isArray(record?.evidence)
    ? record.evidence.flatMap((entry) => {
        const text = asString(entry)
        return text ? [text] : []
      })
    : []
  return {
    code,
    value: asNumber(record?.value),
    reason: asString(record?.reason),
    evidence,
  }
}

async function loadAvailability(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/macro/series"))
    if (!response.ok) {
      return
    }
    const body = await readJson(response)
    availability.value = asString(asRecord(asRecord(body)?.data)?.availability) ?? ""
  } catch {
    // 只用于数据来源说明，取不到时不影响判断展示。
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  busy.value = true
  failure.value = ""
  assessment.value = null
  try {
    const params = new URLSearchParams()
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const query = params.toString()
    const response = await fetch(apiUrl(`/api/v1/macro/assessments${query ? `?${query}` : ""}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    assessment.value = {
      pitDate: asString(data?.pit_date) ?? "",
      regimeCode: asString(data?.regime_code) ?? "",
      growth: parseScore(data?.growth),
      inflation: parseScore(data?.inflation),
      liquidity: parseScore(data?.liquidity),
      notice: asString(data?.notice) ?? "",
    }
    appliedAsOf.value = asOf.value
    if (!activeDimension.value) {
      activeDimension.value =
        dimensions.value.find((item) => item.evidence.length)?.code ?? dimensions.value[0]?.code ?? ""
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  } finally {
    busy.value = false
  }
}

function resetAsOf(): void {
  asOf.value = ""
  void load()
}

onMounted(() => {
  void loadAvailability()
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader title="全球宏观分析" subtitle="把握全球经济周期，洞察宏观变量，发现大类资产配置机会">
      <template #actions>
        <el-date-picker
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日（留空为今天）"
          class="asof-select"
        />
        <el-button type="primary" :loading="busy" @click="load">按观察日重新判断</el-button>
        <el-button v-if="appliedAsOf" @click="resetAsOf">回到今天</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="scenarios" />
      </template>
      <template #filters>
        <span class="source-hint"><i aria-hidden="true" />数据来源：{{ sourceLabel }}</span>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock v-if="phase === 'loading'" state="loading" message="正在计算宏观规则判断" />

      <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

      <template v-else>
        <StateBlock v-if="!assessment" state="empty" message="暂无规则判断结果" />

        <template v-else>
          <StateBlock v-if="allInsufficient" state="empty" :message="insufficientText" />

          <div class="grid-4">
            <MetricTile
              label="组合状态"
              :value="allInsufficient ? '数据不足' : regimeShort"
              :period="`增长 / 通胀 / 流动性 · 判断日 ${assessment.pitDate || '—'}`"
              :info="`由增长、通胀、流动性三个维度的方向组合而成：${regimeLabel}`"
              delta-label="口径"
            >
              <template #delta>
                <StatusPill :text="regimePill.text" :tone="regimePill.tone" />
              </template>
            </MetricTile>
            <MetricTile
              v-for="row in evidenceRows.filter((item) => item.score)"
              :key="row.code"
              :label="`${row.label}维度`"
              :value="row.score?.value ?? '—'"
              :unit="row.score?.value !== null && row.score?.value !== undefined ? '分' : ''"
              :period="DIMENSION_SERIES[row.code]?.name ?? ''"
              info="70 = 上行/偏松，30 = 下行/偏紧，50 = 持平"
              delta-label="方向"
            >
              <template #delta>
                <StatusPill :text="scorePill(row.score).text" :tone="scorePill(row.score).tone" />
              </template>
            </MetricTile>
          </div>

          <div class="grid-2-1">
            <PanelCard title="情景推演" info="按规则列出每个维度的切换条件，不给概率、不预测数值">
              <template #icon><span class="panel-icon">◎</span></template>
              <ul v-if="scenarioRows.length" class="scenario-list">
                <li v-for="item in scenarioRows" :key="item.key" class="scenario-row">
                  <StatusPill :text="item.label" :tone="item.tone" />
                  <div class="scenario-body">
                    <p class="scenario-title">{{ item.title }}</p>
                    <p class="scenario-basis">触发条件：{{ item.basis }}</p>
                  </div>
                </li>
              </ul>
              <StateBlock v-else state="empty" message="数据不足，暂无情景推演" />
              <p class="note">
                {{ assessment.notice || "这是规则计算，不是经济定论。" }} 情景只描述切换条件，不给概率，不构成投资建议。
              </p>
            </PanelCard>

            <PanelCard title="判断依据" info="每个维度使用的最近两期已公布数据">
              <template #icon><span class="panel-icon">✦</span></template>
              <div class="evidence-tabs">
                <button
                  v-for="row in evidenceRows"
                  :key="row.code"
                  type="button"
                  class="evidence-tab"
                  :class="{ active: activeEvidence?.code === row.code }"
                  @click="activeDimension = row.code"
                >
                  {{ row.label }}
                  <span class="tabular">{{ row.score?.value ?? "—" }}</span>
                </button>
              </div>

              <StateBlock
                v-if="!activeEvidence || !activeEvidence.score"
                state="empty"
                message="该维度暂无判断"
              />
              <template v-else>
                <div class="evidence-head">
                  <span>{{ DIMENSION_SERIES[activeEvidence.code]?.name ?? activeEvidence.label }}</span>
                  <StatusPill
                    :text="scorePill(activeEvidence.score).text"
                    :tone="scorePill(activeEvidence.score).tone"
                  />
                </div>
                <p v-if="!activeEvidence.score.evidence.length" class="muted body">已公布数据不足两期。</p>
                <ul v-else class="evidence">
                  <li v-for="line in activeEvidence.score.evidence" :key="line">
                    <span class="tabular">
                      {{ evidenceLine(DIMENSION_SERIES[activeEvidence.code]?.code ?? "", line).period }}
                    </span>
                    <strong class="tabular">
                      {{ evidenceLine(DIMENSION_SERIES[activeEvidence.code]?.code ?? "", line).value }}
                    </strong>
                    <span
                      v-if="evidenceLine(DIMENSION_SERIES[activeEvidence.code]?.code ?? '', line).published"
                      class="muted"
                    >
                      公布于 {{ evidenceLine(DIMENSION_SERIES[activeEvidence.code]?.code ?? "", line).published }}
                    </span>
                  </li>
                </ul>
              </template>
            </PanelCard>
          </div>

          <div class="grid-2">
            <PanelCard title="规则引用的序列" info="打分只用下面三条序列">
              <template #icon><span class="panel-icon">▤</span></template>
              <el-table :data="RULE_SERIES">
                <el-table-column prop="dimension" label="维度" width="80" />
                <el-table-column prop="name" label="序列" min-width="150" />
                <el-table-column prop="code" label="FRED 代码" width="110" />
                <el-table-column prop="rule" label="打分规则" min-width="200" />
              </el-table>
              <p class="note">
                各序列的完整走势见 <router-link to="/macro/us">美国经济</router-link> 与
                <router-link to="/macro/liquidity">货币与流动性</router-link>。
              </p>
            </PanelCard>

            <PanelCard title="判断口径" info="规则判断的适用范围">
              <template #icon><span class="panel-icon">ⓘ</span></template>
              <ul class="doc-list">
                <li>比较每条序列最近两期已公布值的方向：上行、下行或持平，不做幅度外推。</li>
                <li>只使用观察日当天已经公布的数据，避免用到未来信息。</li>
                <li v-if="availability === 'public_csv'">
                  当前数据为 FRED 公开 CSV 的最新修订值，选择过去的观察日将无法得到判断。
                </li>
                <li>中国宏观数据尚未纳入规则；导入后可在 <router-link to="/macro/china">中国经济</router-link> 页查看。</li>
                <li>本页不给概率、不预测点位，不构成投资建议。</li>
              </ul>
            </PanelCard>
          </div>

          <RegimeHistoryPanel />

          <SourceFooter
            :as-of="latestPublished || assessment.pitDate || '—'"
            :sources="[sourceLabel]"
            :stale="allInsufficient"
            :note="`判断日 ${assessment.pitDate || '—'}；观察日留空表示截至今天。`"
          />
        </template>
      </template>
    </div>
  </main>
</template>

<style scoped>
.asof-select {
  width: 200px;
}

.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.source-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.source-hint i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.scenario-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.scenario-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
}

.scenario-row:last-child {
  border-bottom: 0;
}

.scenario-body {
  min-width: 0;
}

.scenario-title {
  margin: 0 0 2px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.scenario-basis {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}

.note {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.body {
  font-size: 13px;
}

.evidence-tabs {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}

.evidence-tab {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}

.evidence-tab.active {
  border-color: var(--accent);
  color: var(--text);
}

.evidence-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.evidence {
  margin: 0;
  padding: 0;
  list-style: none;
}

.evidence li {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.evidence strong {
  color: var(--text-strong);
}

.evidence .muted {
  margin-left: auto;
}

.el-table {
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
