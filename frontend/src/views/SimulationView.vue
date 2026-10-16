<script setup lang="ts">
/**
 * 多角色研究推演（`/simulation/mvp`），流程参考 TradingAgents：
 * 分析师团队 → 多空辩论 → 交易员提案 → 风控三方 → 组合经理，外加记忆反思与对照实验。
 * 所有评分、收益与超额都来自 `/api/v1/simulation/*`，本页只排版；缺数据写「—」或「无评级」。
 */
import { computed, onMounted, reactive, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import {
  RATING_LABEL,
  VERDICT_LABEL,
  createRun,
  fetchReport,
  fetchRun,
  fetchRuns,
  ratingTone,
  settleRun,
  signedPercent,
  type RunRequest,
  type RunSummary,
  type SimulationRun,
} from "../api/simulation"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { restoreSession, useSessionStore } from "../stores/auth"
import CitationChips from "./parts/simulation/CitationChips.vue"
import SimAnalystCard from "./parts/simulation/SimAnalystCard.vue"
import SimDebate from "./parts/simulation/SimDebate.vue"
import SimExperiment from "./parts/simulation/SimExperiment.vue"
import SimPipeline from "./parts/simulation/SimPipeline.vue"
import SimRisk from "./parts/simulation/SimRisk.vue"

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

const refresh = async (): Promise<string | null> => ((await restoreSession()) ? session.accessToken : null)

function today(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, "0")
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

const form = reactive<RunRequest>({
  symbol: "600519.SH",
  as_of: today(),
  max_debate_rounds: 2,
  holding_days: 30,
  risk_strictness: "moderate",
  use_memory: true,
  engine: "rules",
})

const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const message = ref("")
const run = ref<SimulationRun | null>(null)
const history = ref<RunSummary[]>([])
const historyState = ref<"loading" | "ready" | "error">("loading")
const settling = ref(false)
const settleMessage = ref("")
const exporting = ref(false)
const suggestions = ref<InstrumentHit[]>([])

let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => form.symbol,
  (value) => {
    clearTimeout(searchTimer)
    const query = value.trim()
    if (query.length < 2 || suggestions.value.some((hit) => hit.displaySymbol === query)) {
      return
    }
    searchTimer = setTimeout(async () => {
      try {
        suggestions.value = (await searchInstruments(query, undefined, 8)).results.filter(
          (hit) => hit.instrumentType === "STOCK",
        )
      } catch {
        suggestions.value = []
      }
    }, 250)
  },
)

async function loadHistory(): Promise<void> {
  try {
    history.value = await fetchRuns(session.accessToken, refresh)
    historyState.value = "ready"
  } catch {
    historyState.value = "error"
  }
}

async function open(id: string): Promise<void> {
  phase.value = "loading"
  message.value = ""
  settleMessage.value = ""
  try {
    run.value = await fetchRun(id, session.accessToken, refresh)
    phase.value = run.value ? "ready" : "error"
    if (run.value && route.query.run !== id) {
      void router.replace({ query: { ...route.query, run: id } })
    }
  } catch (caught) {
    phase.value = "error"
    message.value = caught instanceof Error ? caught.message : "无法连接接口"
  }
}

async function start(): Promise<void> {
  phase.value = "loading"
  message.value = ""
  settleMessage.value = ""
  try {
    const created = await createRun({ ...form, symbol: form.symbol.trim().toUpperCase() }, session.accessToken, refresh)
    run.value = created
    phase.value = created ? "ready" : "error"
    if (created) {
      void router.replace({ query: { ...route.query, run: created.runId } })
      void loadHistory()
    } else {
      message.value = "接口没有返回推演结果"
    }
  } catch (caught) {
    phase.value = "error"
    message.value = caught instanceof Error ? caught.message : "无法连接接口"
  }
}

async function settle(): Promise<void> {
  if (!run.value) {
    return
  }
  settling.value = true
  settleMessage.value = ""
  try {
    const evaluation = await settleRun(run.value.runId, session.accessToken, refresh)
    if (evaluation) {
      run.value = { ...run.value, evaluation }
      void loadHistory()
    }
  } catch (caught) {
    settleMessage.value = caught instanceof Error ? caught.message : "结算失败"
  } finally {
    settling.value = false
  }
}

async function exportReport(): Promise<void> {
  if (!run.value) {
    return
  }
  exporting.value = true
  try {
    const markdown = await fetchReport(run.value.runId, session.accessToken, refresh)
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }))
    const link = document.createElement("a")
    link.href = url
    link.download = `推演-${run.value.symbol}-${run.value.asOf}.md`
    link.click()
    URL.revokeObjectURL(url)
  } catch (caught) {
    settleMessage.value = caught instanceof Error ? caught.message : "导出失败"
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  await loadHistory()
  const wanted = typeof route.query.run === "string" ? route.query.run : history.value[0]?.runId
  if (wanted) {
    await open(wanted)
  }
})

const tone = computed(() => ratingTone(run.value?.rating ?? ""))
const legacy = computed(() => Boolean(run.value && !run.value.analysts.length))
const finalProposal = computed(() => run.value?.proposals.at(-1) ?? null)
const coveredCount = computed(() => run.value?.analysts.filter((item) => item.score !== null).length ?? 0)
const sources = computed(() => {
  const found = new Set<string>()
  for (const analyst of run.value?.analysts ?? []) {
    for (const finding of analyst.findings) {
      for (const cite of finding.citations) {
        found.add(cite.source)
      }
    }
  }
  return [...found]
})

const evaluationTone = (verdict: string | null): "danger" | "ok" | "info" | "muted" =>
  verdict === "accurate" ? "ok" : verdict === "divergent" ? "danger" : verdict ? "info" : "muted"

function excessClass(value: number | null): string {
  return value === null || value === 0 ? "" : value > 0 ? "rise" : "fall"
}
</script>

<template>
  <div class="page">
    <PageHeader title="多角色研究推演" subtitle="分析师、多空研究员、交易员、风控与组合经理分工协作，只读取所选日期及以前的数据">
      <template #actions>
        <StatusPill
          v-if="run"
          :tone="run.llm ? 'accent' : 'muted'"
          :text="run.llm ? '语言模型参与' : '规则引擎 · 未调用语言模型'"
          dot
        />
      </template>
    </PageHeader>

    <div class="top">
      <PanelCard title="推演参数" tone="accent">
        <form class="config" @submit.prevent="start">
          <label class="field">
            <span>标的代码或名称</span>
            <input v-model="form.symbol" name="symbol" list="sim-symbols" autocomplete="off" required />
            <datalist id="sim-symbols">
              <option v-for="hit in suggestions" :key="hit.id" :value="hit.displaySymbol">{{ hit.nameCn || hit.nameEn }}</option>
            </datalist>
          </label>
          <label class="field">
            <span>推演日期（时间旅行）</span>
            <input v-model="form.as_of" type="date" name="as_of" :max="today()" required />
          </label>
          <label class="field">
            <span>辩论轮数上限</span>
            <select v-model.number="form.max_debate_rounds" name="rounds">
              <option :value="1">1 轮</option>
              <option :value="2">2 轮</option>
              <option :value="3">3 轮</option>
            </select>
          </label>
          <label class="field">
            <span>持有期（结算用）</span>
            <select v-model.number="form.holding_days" name="holding">
              <option :value="10">10 天</option>
              <option :value="20">20 天</option>
              <option :value="30">30 天</option>
              <option :value="60">60 天</option>
            </select>
          </label>
          <fieldset class="field segmented">
            <legend>风控尺度</legend>
            <label :class="{ on: form.risk_strictness === 'moderate' }">
              <input v-model="form.risk_strictness" type="radio" value="moderate" name="strictness" />标准
            </label>
            <label :class="{ on: form.risk_strictness === 'conservative' }">
              <input v-model="form.risk_strictness" type="radio" value="conservative" name="strictness" />从严
            </label>
          </fieldset>
          <label class="field">
            <span>推演引擎</span>
            <select v-model="form.engine" name="engine">
              <option value="rules">规则引擎（可复现）</option>
              <option value="tradingagents">TradingAgents（需配置语言模型）</option>
            </select>
          </label>
          <label class="check">
            <input v-model="form.use_memory" type="checkbox" name="memory" />
            <span>调用历史记忆（只用持有期已走完的旧推演）</span>
          </label>
          <button class="primary" type="submit" :disabled="phase === 'loading'">
            {{ phase === "loading" ? "推演中…" : "开始推演" }}
          </button>
        </form>
      </PanelCard>

      <PanelCard title="协作流程" info="节点状态来自本次推演的结构化输出；虚线节点表示该维度没有可用数据">
        <SimPipeline :run="run" :running="phase === 'loading'" />
        <div v-if="run" class="run-meta tabular">
          <span>{{ run.symbol }} {{ run.subjectName }}</span>
          <span v-if="run.industry">{{ run.industry }}</span>
          <span>截至 {{ run.asOf }}</span>
          <span>{{ coveredCount }}/4 维度有数据</span>
          <span v-if="run.durationMs !== null">耗时 {{ (run.durationMs / 1000).toFixed(1) }} 秒</span>
        </div>
      </PanelCard>
    </div>

    <StateBlock v-if="phase === 'loading' && !run" state="loading" title="推演中" :rows="4" />
    <StateBlock v-else-if="phase === 'error'" state="error" title="推演没有完成" :message="message" />
    <StateBlock
      v-else-if="phase === 'idle' && historyState !== 'loading'"
      state="empty"
      title="还没有推演记录"
      message="选择标的与日期后开始第一次推演"
    />

    <template v-if="run && phase !== 'error'">
      <section class="hero" :class="`tone-${tone}`" aria-label="组合经理决议">
        <div class="verdict">
          <span class="eyebrow">组合经理决议</span>
          <strong class="rating">{{ run.rating === "REVIEW" ? "—" : run.rating }}</strong>
          <span class="rating-cn">{{ RATING_LABEL[run.rating] ?? run.rating }}</span>
          <span v-if="run.aborted" class="aborted">风控连续否决后强制防御</span>
        </div>
        <p v-if="legacy" class="legacy">
          这是改版前的推演记录，只保存了评级与多空摘要，没有分析师评分、引用与风控记录。重新推演即可得到完整的结构化结果。
        </p>
        <dl v-else class="facts">
          <div>
            <dt>置信度</dt>
            <dd class="tabular">{{ run.confidence === null ? "—" : run.confidence.toFixed(1) }}</dd>
          </div>
          <div>
            <dt>参考配置</dt>
            <dd>{{ run.allocation ?? "—" }}</dd>
          </div>
          <div>
            <dt>关注期</dt>
            <dd>{{ run.holdingDays === null ? (run.horizon ?? "—") : `${run.holdingDays} 天` }}</dd>
          </div>
          <div class="wide">
            <dt>失效条件</dt>
            <dd>{{ run.stopLoss ?? finalProposal?.stopLoss ?? "—" }}</dd>
          </div>
        </dl>
        <p class="decision">{{ run.decision }}</p>
        <div v-if="!legacy" class="drivers">
          <div>
            <h3>主要支撑</h3>
            <ul>
              <li v-for="(item, index) in run.drivers" :key="index">
                {{ item.text }}<CitationChips :items="item.citations" />
              </li>
              <li v-if="!run.drivers.length" class="none">没有带引用的支撑证据</li>
            </ul>
          </div>
          <div>
            <h3>主要风险</h3>
            <ul>
              <li v-for="(item, index) in run.riskFactors" :key="index">
                {{ item.text }}<CitationChips :items="item.citations" />
              </li>
              <li v-if="!run.riskFactors.length" class="none">没有带引用的风险证据</li>
            </ul>
          </div>
        </div>
        <div class="hero-actions">
          <button type="button" class="ghost" :disabled="exporting" @click="exportReport">
            {{ exporting ? "导出中…" : "导出 Markdown 报告" }}
          </button>
        </div>
      </section>

      <PanelCard v-if="!legacy" title="分析师团队" info="四位分析师各自只看自己负责的数据；评分 50 为中性，没有数据的维度不出分">
        <div class="analysts">
          <SimAnalystCard v-for="item in run.analysts" :key="item.analyst" :item="item" />
        </div>
      </PanelCard>

      <div class="split">
        <PanelCard title="多空辩论" info="每轮针对对方论点反驳；没有引用的论点会被主导研究员剔除，观点收敛即提前结束">
          <SimDebate :debates="run.debates" :memo="run.memo" />
        </PanelCard>

        <PanelCard title="记忆与结算" tone="violet">
          <div class="settle">
            <template v-if="run.evaluation && run.evaluation.qualityFlag !== 'insufficient'">
              <div class="settle-grid">
                <div>
                  <span>区间收益</span>
                  <strong class="tabular" :class="excessClass(run.evaluation.actualReturn)">
                    {{ signedPercent(run.evaluation.actualReturn) }}
                  </strong>
                </div>
                <div>
                  <span>{{ run.evaluation.benchmarkSymbol ?? "基准" }}</span>
                  <strong class="tabular" :class="excessClass(run.evaluation.benchmarkReturn)">
                    {{ signedPercent(run.evaluation.benchmarkReturn) }}
                  </strong>
                </div>
                <div>
                  <span>超额</span>
                  <strong class="tabular" :class="excessClass(run.evaluation.excessReturn)">
                    {{ signedPercent(run.evaluation.excessReturn) }}
                  </strong>
                </div>
              </div>
              <p class="settle-line">
                <StatusPill
                  :tone="evaluationTone(run.evaluation.verdict)"
                  :text="VERDICT_LABEL[run.evaluation.verdict ?? ''] ?? '未判定'"
                />
                <span class="tabular">
                  持有期 {{ run.evaluation.holdingDays ?? "—" }} 天，出场 {{ run.evaluation.exitDate ?? "—" }}
                </span>
              </p>
              <p v-if="run.evaluation.reflection" class="reflection">{{ run.evaluation.reflection }}</p>
            </template>
            <p v-else-if="run.evaluation" class="pending">{{ run.evaluation.note }}</p>
            <p v-else class="pending">尚未结算。持有期走完后，用入库收盘价计算相对沪深300的超额。</p>
            <button type="button" class="ghost" :disabled="settling" @click="settle">
              {{
                settling
                  ? "结算中…"
                  : run.evaluation && run.evaluation.qualityFlag !== "insufficient"
                    ? "重新结算"
                    : "结算这次推演"
              }}
            </button>
            <p v-if="settleMessage" class="error">{{ settleMessage }}</p>

            <h3>本次调用的历史记忆</h3>
            <ul v-if="run.memory.length" class="memory">
              <li v-for="item in run.memory" :key="item.asOf + item.rating">
                <span class="tabular">{{ item.asOf }} · {{ RATING_LABEL[item.rating] ?? item.rating }}</span>
                <span class="tabular" :class="excessClass(item.excessReturn)">{{ signedPercent(item.excessReturn) }}</span>
                <p>{{ item.reflection }}</p>
              </li>
            </ul>
            <p v-else class="pending">没有可用的已结算旧推演（或本次未开启记忆）。</p>
          </div>
        </PanelCard>
      </div>

      <PanelCard v-if="!legacy" title="风控委员会" info="激进、中性、保守三方各自给出压力情景；保守风控可一票否决，交易员最多修正两次">
        <SimRisk :proposals="run.proposals" :risks="run.risks" :aborted="run.aborted" />
      </PanelCard>
    </template>

    <div class="split bottom">
      <PanelCard title="对照实验" info="同一网格上比较多角色推演与单一技术面信号，按评级统计相对沪深300的平均超额">
        <SimExperiment :token="session.accessToken" :refresh="refresh" :default-symbol="form.symbol" />
      </PanelCard>

      <PanelCard title="推演记录">
        <StateBlock v-if="historyState === 'loading'" state="loading" :rows="4" />
        <StateBlock v-else-if="historyState === 'error'" state="error" message="无法读取推演记录" />
        <StateBlock v-else-if="!history.length" state="empty" message="还没有推演记录" />
        <ul v-else class="history">
          <li v-for="item in history" :key="item.runId">
            <button type="button" :class="{ active: item.runId === run?.runId }" @click="open(item.runId)">
              <span class="h-main">
                <span class="tabular">{{ item.symbol }}</span>
                <span class="h-rating" :class="ratingTone(item.rating)">{{ RATING_LABEL[item.rating] ?? item.rating }}</span>
              </span>
              <span class="h-sub tabular">
                <span>截至 {{ item.asOf }}</span>
                <span :class="excessClass(item.excessReturn)">{{
                  item.excessReturn === null ? "未结算" : `超额 ${signedPercent(item.excessReturn)}`
                }}</span>
              </span>
            </button>
          </li>
        </ul>
      </PanelCard>
    </div>

    <SourceFooter
      :as-of="run?.asOf ?? ''"
      :sources="sources"
      :note="run?.disclaimer || '研究推演仅用于方法验证，不构成投资建议，不连接任何交易通道'"
    />
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: var(--gap);
}

.top {
  display: grid;
  grid-template-columns: minmax(300px, 0.9fr) 2.1fr;
  gap: var(--gap);
}

.config {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 12px;
}

.field {
  display: grid;
  gap: 4px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
  font-size: 12px;
  color: var(--muted);
}

.field:first-child {
  grid-column: 1 / -1;
}

.field input,
.field select {
  height: 32px;
  padding: 0 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-deep);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  min-width: 0;
}

.field input:focus-visible,
.field select:focus-visible,
button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.segmented {
  grid-template-columns: 1fr 1fr;
  gap: 0;
}

.segmented legend {
  margin-bottom: 4px;
}

.segmented label {
  display: grid;
  place-items: center;
  height: 32px;
  border: 1px solid var(--line);
  background: var(--panel-deep);
  color: var(--muted);
  font-size: 13px;
  cursor: pointer;
}

.segmented label:first-of-type {
  border-radius: var(--radius-sm) 0 0 var(--radius-sm);
}

.segmented label:last-of-type {
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  border-left: 0;
}

.segmented label.on {
  background: var(--accent-soft);
  color: var(--text-strong);
  border-color: var(--edge-light);
}

.segmented input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.check {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}

.primary {
  grid-column: 1 / -1;
  height: 36px;
  border-radius: var(--radius-sm);
  border: 0;
  background: linear-gradient(90deg, var(--accent-strong), var(--accent));
  color: #fff;
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.primary:disabled {
  opacity: 0.65;
  cursor: progress;
}

.ghost {
  justify-self: start;
  height: 30px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.ghost:hover:not(:disabled) {
  border-color: var(--edge-light);
}

.ghost:disabled {
  opacity: 0.6;
}

.run-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
  font-size: 12px;
  color: var(--muted);
}

.hero {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 16px 28px;
  padding: 18px 20px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background:
    radial-gradient(120% 140% at 0% 0%, var(--hero-glow, var(--accent-soft)), transparent 55%),
    var(--panel);
  box-shadow: var(--shadow-card);
}

.tone-rise {
  --hero-color: var(--rise);
  --hero-glow: color-mix(in srgb, var(--rise) 14%, transparent);
}

.tone-fall {
  --hero-color: var(--fall);
  --hero-glow: color-mix(in srgb, var(--fall) 14%, transparent);
}

.tone-flat,
.tone-none {
  --hero-color: var(--text-strong);
}

.verdict {
  display: grid;
  align-content: start;
  gap: 2px;
  min-width: 160px;
}

.eyebrow {
  font-size: 12px;
  color: var(--muted);
}

.rating {
  font-family: var(--font-num);
  font-size: 40px;
  line-height: 1.1;
  color: var(--hero-color);
}

.rating-cn {
  font-size: 15px;
  color: var(--hero-color);
}

.aborted {
  margin-top: 6px;
  font-size: 12px;
  color: var(--warn);
}

.legacy {
  margin: 0;
  align-self: center;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) 2fr;
  gap: 12px;
  margin: 0;
  align-content: start;
}

.facts dt {
  font-size: 12px;
  color: var(--muted);
}

.facts dd {
  margin: 4px 0 0;
  font-size: 15px;
  color: var(--text-strong);
}

.facts .wide dd {
  font-size: 13px;
  line-height: 1.5;
}

.decision {
  grid-column: 1 / -1;
  max-width: 72ch;
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
}

.drivers {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.drivers h3,
.settle h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.drivers ul {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}

.none {
  color: var(--muted);
  list-style: none;
  margin-left: -18px;
}

.hero-actions {
  grid-column: 1 / -1;
}

.analysts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.split {
  display: grid;
  grid-template-columns: 1.7fr 1fr;
  gap: var(--gap);
  align-items: start;
}

.settle {
  display: grid;
  gap: 10px;
}

.settle-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.settle-grid div {
  display: grid;
  gap: 4px;
  padding: 10px;
  border-radius: var(--radius-sm);
  background: var(--panel-deep);
}

.settle-grid span {
  font-size: 12px;
  color: var(--muted);
}

.settle-grid strong {
  font-family: var(--font-num);
  font-size: 20px;
  color: var(--text-strong);
}

.settle-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.reflection,
.pending {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.pending {
  color: var(--muted);
}

.error {
  margin: 0;
  font-size: 12px;
  color: var(--danger);
}

.memory {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.memory li {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 8px;
  padding: 8px 0;
  border-top: 1px solid var(--line-soft);
  font-size: 12px;
  color: var(--muted);
}

.memory p {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text);
}

.rise,
.settle-grid strong.rise {
  color: var(--rise);
}

.fall,
.settle-grid strong.fall {
  color: var(--fall);
}

.history {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
  max-height: 420px;
  overflow: auto;
}

.history button {
  display: grid;
  gap: 2px;
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.history button:hover {
  background: var(--panel-soft);
}

.history button.active {
  border-color: var(--edge-light);
  background: var(--accent-soft);
}

.h-main,
.h-sub {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}

.h-main {
  font-size: 13px;
}

.h-sub {
  font-size: 12px;
  color: var(--muted);
}

.h-rating.flat,
.h-rating.none {
  color: var(--muted);
}

@media (max-width: 1280px) {
  .analysts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 18px;
  }

  .analysts > :nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }

  .facts {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .facts .wide {
    grid-column: 1 / -1;
  }
}

@media (max-width: 1100px) {
  .top,
  .split {
    grid-template-columns: 1fr;
  }

  .hero {
    grid-template-columns: 1fr;
  }

  .drivers {
    grid-template-columns: 1fr;
  }
}
</style>
