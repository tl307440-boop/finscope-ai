<script setup lang="ts">
/**
 * 宏观综合判断（/macro/scenarios，效果图 2 的「宏观综合判断」页签内容）。
 *
 * 真实接口（与 backend/app/schemas/macro.py 的 AssessmentData、
 * backend/app/services/macro_rules.py 一致；原有取数逻辑保持不变）：
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value,
 *               reason, evidence[] }, notice, version }
 *      · value 为 int，但**取不到两个已公布观测值时字段缺省**（实测 null）
 *      · 打分规则 macro-rules-v1：比较该序列最近两个已公布观测值，
 *        上行→增长/通胀记 70、流动性（联邦基金利率，tighter_when_rising）记 30；
 *        下行→70/30 互换；持平→50；不足两点→value=null、reason="insufficient_data"
 *      · 组合状态 regime_code = "<增长>-<通胀>-<流动性>"，≥50 记 up、流动性记 easy，
 *        否则 down / tight；任一维度取不到值时为 "insufficient_data"
 *
 * 只读边界（如实说明，不伪造结论）：
 *  - **后端的宏观接口里没有 POST /api/v1/macro/assessments**（openapi.json 与
 *    backend/app/api/v1/macro.py 都只有 GET；唯一的 POST 是仅管理员的
 *    /api/v1/macro/china/observations 中国宏观 CSV 导入）。因此本页以只读展示为主，
 *    效果图里的情景输入表单在这里没有可用的写入端，改为「情景矩阵 + 证据抽屉」：
 *    情景矩阵只是把当前 regime_code 的取值位置与切换条件摆出来，不生成任何前端自造结论。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString } from "../api/json"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"

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
  version: string
}

/** 规则口径引用的序列；与 backend/app/services/macro_rules.py 第 36-38 行一致。 */
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
const asOf = ref("")
const appliedAsOf = ref("")

const dimensions = computed<DimensionScore[]>(() => {
  const current = assessment.value
  if (!current) {
    return []
  }
  return [current.growth, current.inflation, current.liquidity].flatMap((item) =>
    item ? [item] : [],
  )
})

const hasAnyScore = computed<boolean>(() => dimensions.value.some((item) => item.value !== null))

const allInsufficient = computed<boolean>(
  () => dimensions.value.length > 0 && dimensions.value.every((item) => item.value === null),
)

const regimeLabel = computed<string>(() => {
  const code = assessment.value?.regimeCode ?? ""
  if (!code) {
    return "—"
  }
  if (code === "insufficient_data") {
    return "数据不足，无法给出组合状态"
  }
  const [growth, inflation, liquidity] = code.split("-")
  const words: Record<string, string> = {
    up: "上行",
    down: "下行",
    easy: "偏松",
    tight: "偏紧",
  }
  const parts: string[] = []
  if (growth) {
    parts.push(`增长${words[growth] ?? growth}`)
  }
  if (inflation) {
    parts.push(`通胀${words[inflation] ?? inflation}`)
  }
  if (liquidity) {
    parts.push(`流动性${words[liquidity] ?? liquidity}`)
  }
  return parts.join(" · ")
})

const regimePill = computed<{ text: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" }>(() => {
  const current = assessment.value
  if (!current || !current.regimeCode) {
    return { text: "未取到", tone: "muted" }
  }
  if (current.regimeCode === "insufficient_data" || allInsufficient.value) {
    return { text: "数据不足", tone: "muted" }
  }
  return { text: current.regimeCode, tone: "accent" }
})

/**
 * 情景矩阵：把「当前 regime_code 落在哪一格、什么条件下切到另一格」摆出来。
 * 这里的描述只引用后端分数的取值域（≥50 / <50、≥70 / ≤30），不预测未来数值。
 */
const SCENARIO_ROWS = [
  {
    key: "base",
    label: "基准",
    tone: "accent" as const,
    title: "沿用当前组合状态",
    basis: "以本次请求的 regime_code 作为基准情形，不做外推、不给概率。",
  },
  {
    key: "reflation",
    label: "增长走强",
    tone: "ok" as const,
    title: "增长分数上到 ≥50（up）",
    basis: "需要后续观测值把增长维度推到上行档；届时 regime_code 的第一段会从 down 变为 up。",
  },
  {
    key: "tightening",
    label: "流动性转紧",
    tone: "warn" as const,
    title: "流动性分数落到 ≤30（tight）",
    basis: "联邦基金利率上行时该维度记 30；届时 regime_code 的第三段会从 easy 变为 tight。",
  },
  {
    key: "deflation",
    label: "通胀回落",
    tone: "danger" as const,
    title: "通胀分数落到 ≤30（down）",
    basis: "需要后续观测值把通胀维度压到下行档；届时 regime_code 的第二段会从 up 变为 down。",
  },
]

/** 证据抽屉：哪一个维度展开在右侧，默认打开第一个拿到分数的维度。 */
const activeDimension = ref<string>("")

const evidenceRows = computed<{ code: string; label: string; score: DimensionScore | null }[]>(() =>
  ["growth", "inflation", "liquidity"].map((code) => ({
    code,
    label: code === "growth" ? "增长" : code === "inflation" ? "通胀" : "流动性",
    score:
      code === "growth"
        ? assessment.value?.growth ?? null
        : code === "inflation"
          ? assessment.value?.inflation ?? null
          : assessment.value?.liquidity ?? null,
  })),
)

const activeEvidence = computed(() => {
  const found = evidenceRows.value.find((row) => row.code === activeDimension.value)
  return found ?? evidenceRows.value[0] ?? null
})

/**
 * 后端只回分数不回文案。方向词按维度区分（流动性是 tighter_when_rising）：
 * 增长/通胀 70=上行、30=下行；流动性 30=利率上行＝偏紧、70=利率下行＝偏松。
 * 配色沿用全站「涨红跌绿」：方向向上（增长/通胀上行、流动性收紧）用 --rise，
 * 方向向下用 --fall，持平用正文色。
 */
function scoreView(score: DimensionScore | null): { label: string; tone: string } {
  if (!score || score.value === null) {
    return { label: "数据不足（value 缺省）", tone: "var(--muted)" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value === 70) {
    return {
      label: liquidity ? "偏松（70，利率下行）" : "上行（70）",
      tone: "var(--rise)",
    }
  }
  if (score.value === 30) {
    return {
      label: liquidity ? "偏紧（30，利率上行）" : "下行（30）",
      tone: "var(--fall)",
    }
  }
  if (score.value === 50) {
    return { label: "持平（50）", tone: "var(--text)" }
  }
  return { label: `分数 ${score.value}`, tone: "var(--text)" }
}

function scorePill(score: DimensionScore | null): { text: string; tone: "ok" | "warn" | "danger" | "muted" } {
  if (!score || score.value === null) {
    return { text: "数据不足", tone: "muted" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value === 70) {
    return { text: liquidity ? "偏松" : "上行", tone: liquidity ? "ok" : "danger" }
  }
  if (score.value === 30) {
    return { text: liquidity ? "偏紧" : "下行", tone: liquidity ? "danger" : "ok" }
  }
  return { text: "持平", tone: "warn" }
}

function reasonView(reason: string | null): string {
  if (!reason) {
    return ""
  }
  if (reason === "insufficient_data") {
    return "不足两个已公布观测值"
  }
  return reason
}

/** 依据行来自后端 _evidence()："<序列代码> <观测日> <数值> 公布日 <公布日>"。 */
function evidenceParts(line: string): { headline: string; published: string } {
  const marker = " 公布日 "
  const index = line.indexOf(marker)
  if (index < 0) {
    return { headline: line, published: "" }
  }
  return { headline: line.slice(0, index), published: line.slice(index + marker.length) }
}

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
      version: asString(data?.version) ?? "",
    }
    appliedAsOf.value = asOf.value
    if (!activeDimension.value) {
      activeDimension.value =
        dimensions.value.find((item) => item.evidence.length)?.code ?? dimensions.value[0]?.code ?? ""
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
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
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="全球宏观分析"
      subtitle="把握全球经济周期，洞察宏观变量，发现大类资产配置机会"
    >
      <template #actions>
        <el-date-picker
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日（留空＝今天）"
          class="asof-select"
        />
        <el-button type="primary" :loading="busy" @click="load">按该观察日重新计算</el-button>
        <el-button v-if="appliedAsOf" @click="resetAsOf">回到今天</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="scenarios" />
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        message="正在计算宏观情景规则分数"
      />

      <StateBlock
        v-else-if="phase === 'error'"
        state="error"
        :message="failure"
        @retry="load"
      />

      <template v-else>
        <p class="muted lead">
          本页展示的是后端规则口径（macro-rules-v1）在指定观察日算出的分数，不是经济预测，也不是投资建议。
          观察日按后端时点过滤：只使用当天已经公布的观测值（realtime_start 早于观察日），避免用到未来数据。
          当前请求观察日：{{ appliedAsOf || "今天" }}。
        </p>

        <StateBlock
          v-if="!assessment"
          state="empty"
          message="未取到评估结果，本页不编造结论。"
        />

        <template v-else>
          <div class="grid-4">
            <MetricTile
              label="组合状态 regime_code"
              :value="assessment.regimeCode || '—'"
              :period="`后端时点 ${assessment.pitDate || '—'}`"
              info="regime_code = &lt;增长&gt;-&lt;通胀&gt;-&lt;流动性&gt;，任一维度取不到值时为 insufficient_data"
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
              :period="row.score?.reason ? reasonView(row.score.reason) : '已取到分数'"
              info="70 = 上行/偏松，30 = 下行/偏紧，50 = 持平；value 缺省表示不足两个已公布观测值"
            >
              <template #delta>
                <StatusPill :text="scorePill(row.score).text" :tone="scorePill(row.score).tone" />
              </template>
            </MetricTile>
          </div>

          <p class="muted">
            中文解读：{{ regimeLabel }} · 规则版本 {{ assessment.version || "—" }}。
          </p>

          <div class="grid-2-1">
            <PanelCard
              title="情景矩阵（按当前分数位置与切换条件）"
              info="情景只描述规则分数的取值域与切换条件，不给概率、不预测数值"
            >
              <template #icon><span class="panel-icon">◎</span></template>
              <ul class="scenario-list">
                <li v-for="item in SCENARIO_ROWS" :key="item.key" class="scenario-row">
                  <StatusPill :text="item.label" :tone="item.tone" />
                  <div class="scenario-body">
                    <p class="scenario-title">
                      {{ item.title }}
                      <span class="muted">· 概率：后端未提供概率字段</span>
                    </p>
                    <p class="muted">{{ item.basis }}</p>
                  </div>
                </li>
              </ul>
              <p class="muted">
                效果图里的「基准/乐观/悲观 概率」需要模型或人工情景输入；后端
                <code>/api/v1/macro/assessments</code> 只有 GET，没有该写入口，所以这里不填概率。
              </p>
            </PanelCard>

            <PanelCard title="证据抽屉" info="每个维度的 reason 与 evidence 原样来自后端">
              <template #icon><span class="panel-icon">✦</span></template>
              <div class="grid-3 evidence-tabs">
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
                message="该维度没有返回分数与依据"
              />
              <template v-else>
                <p class="doc-text">
                  {{ activeEvidence.label }}：<span :style="{ color: scoreView(activeEvidence.score).tone }">
                    {{ scoreView(activeEvidence.score).label }}
                  </span>
                </p>
                <p class="muted">
                  reason：<code>{{ activeEvidence.score.reason ?? "（后端未返回）" }}</code>
                  <template v-if="reasonView(activeEvidence.score.reason)">
                    · {{ reasonView(activeEvidence.score.reason) }}
                  </template>
                </p>
                <p v-if="!activeEvidence.score.evidence.length" class="muted">
                  本次没有返回可用依据行。
                </p>
                <ul v-else class="evidence">
                  <li v-for="line in activeEvidence.score.evidence" :key="line">
                    {{ evidenceParts(line).headline }}
                    <span v-if="evidenceParts(line).published" class="muted">
                      （公布日 {{ evidenceParts(line).published }}）
                    </span>
                  </li>
                </ul>
              </template>
            </PanelCard>
          </div>

          <StateBlock
            v-if="allInsufficient"
            state="stale"
            :as-of="assessment.pitDate"
            message="三个维度都取不到分数：逐条列出的序列当前没有足够的已公布观测值（每个维度至少需要两个）。"
            @retry="load"
          />
          <StateBlock
            v-else-if="!hasAnyScore"
            state="empty"
            message="本次没有任何维度拿到分数。"
          />
          <StateBlock
            v-if="assessment.notice"
            state="stale"
            :as-of="assessment.pitDate"
            :message="assessment.notice"
            @retry="load"
          />

          <div class="grid-2">
            <PanelCard title="规则引用的序列" info="打分只用下面三条序列">
              <template #icon><span class="panel-icon">▤</span></template>
              <p class="muted">
                它们在库里的真实观测值可以在
                <router-link to="/macro/liquidity">货币与流动性</router-link> 与
                <router-link to="/macro/china">中国经济宏观</router-link> 页按同一观察日查看。
              </p>
              <el-table :data="RULE_SERIES" stripe>
                <el-table-column prop="dimension" label="维度" width="88" />
                <el-table-column prop="code" label="序列代码" width="130" />
                <el-table-column prop="name" label="名称" min-width="160" />
                <el-table-column prop="rule" label="规则口径" min-width="200" />
              </el-table>
            </PanelCard>

            <PanelCard title="只读说明（判断边界）" info="写入口缺失，如实说明">
              <template #icon><span class="panel-icon">ⓘ</span></template>
              <ul class="doc-list">
                <li>
                  后端宏观接口没有提供「创建情景评估」的写入口（openapi.json 里
                  <code>/api/v1/macro/assessments</code> 只有 GET），所以本页不提供提交表单。
                </li>
                <li>
                  页面不在浏览器端自造结论：分数、理由与依据全部原样来自后端；取不到时显示
                  <code>insufficient_data</code> 与真实原因。
                </li>
                <li>
                  中国宏观序列需管理员 POST <code>/api/v1/macro/china/observations</code> 导入 CSV
                  后才有观测值，导入前相关维度会一直是数据不足。
                </li>
              </ul>
              <p class="muted">
                本页不给概率、不预测点位、不构成投资建议；所有数值可在
                <router-link to="/macro/us">美国经济</router-link> 页核对同一观察日口径。
              </p>
            </PanelCard>
          </div>

          <SourceFooter
            :as-of="assessment.pitDate || '—'"
            :sources="['FRED（圣路易斯联储）', 'macro-rules-v1']"
            :stale="allInsufficient"
            note="分数与理由全部来自后端规则；观察日留空表示截止今天。"
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

.lead {
  margin: 0;
  line-height: 1.7;
}

.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.scenario-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.scenario-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}

.scenario-row:last-child {
  border-bottom: 0;
}

.scenario-body {
  min-width: 0;
}

.scenario-title {
  margin: 0 0 2px;
  font-size: 13px;
}

.evidence-tabs {
  margin-bottom: 10px;
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

.evidence {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--text);
  font-size: 12px;
  line-height: 1.7;
}

.el-table {
  margin-top: 8px;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
