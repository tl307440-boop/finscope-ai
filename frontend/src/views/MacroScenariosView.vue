<script setup lang="ts">
/**
 * 宏观情景分析（/macro/scenarios）。
 *
 * 真实接口（已用 admin 令牌逐一打过；字段以 backend/app/schemas/macro.py 的
 * AssessmentData 与 backend/app/services/macro_rules.py 为准）：
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
 *    /api/v1/macro/china/observations 中国宏观 CSV 导入）。因此本页以只读展示
 *    为主，不提供「创建情景评估」表单，也不生成任何前端自造的结论。
 *  - 页面只做两件事：按观察日取后端规则分数 + 把分数/理由/依据原样呈现。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString } from "../api/json"

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

const hasAnyScore = computed<boolean>(() =>
  dimensions.value.some((item) => item.value !== null),
)

const allInsufficient = computed<boolean>(() =>
  dimensions.value.length > 0 && dimensions.value.every((item) => item.value === null),
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在计算宏观情景规则分数</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取宏观情景评估</h1>
      <p class="muted">{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <section class="panel">
        <h1>宏观情景分析</h1>
        <p class="muted">
          本页展示的是后端规则口径（macro-rules-v1）在指定观察日算出的分数，不是经济预测，也不是投资建议。
        </p>
        <div class="filter-row">
          <el-date-picker
            v-model="asOf"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="观察日（留空＝今天）"
            class="filter-date"
          />
          <el-button type="primary" :loading="busy" @click="load">按该观察日重新计算</el-button>
          <el-button v-if="appliedAsOf" @click="resetAsOf">回到今天</el-button>
        </div>
        <p class="muted">
          观察日按后端时点过滤：只使用当天已经公布的观测值（realtime_start 早于观察日），避免用到未来数据。
          当前请求观察日：{{ appliedAsOf || "今天" }}。
        </p>
        <el-alert type="info" :closable="false" title="只读说明">
          <template #default>
            后端宏观接口没有提供「创建情景评估」的写入口（openapi.json 里
            <code>/api/v1/macro/assessments</code> 只有 GET），所以本页不提供提交表单，
            也不在浏览器端自造结论；分数、理由与依据全部原样来自后端。
          </template>
        </el-alert>
      </section>

      <section class="panel">
        <h2>组合状态</h2>
        <p v-if="!assessment" class="muted">未取到评估结果，本页不编造结论。</p>
        <template v-else>
          <el-descriptions :column="2" border class="macro-descriptions">
            <el-descriptions-item label="组合状态（regime_code）">
              <code>{{ assessment.regimeCode || "—" }}</code>
            </el-descriptions-item>
            <el-descriptions-item label="中文解读">{{ regimeLabel }}</el-descriptions-item>
            <el-descriptions-item label="后端时点">{{ assessment.pitDate || "—" }}</el-descriptions-item>
            <el-descriptions-item label="规则版本">{{ assessment.version || "—" }}</el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="assessment.notice" type="warning" :closable="false" class="macro-alert">
            <template #default>{{ assessment.notice }}</template>
          </el-alert>
        </template>
      </section>

      <section class="panel">
        <h2>三个维度的规则分数</h2>
        <el-empty
          v-if="!assessment || !dimensions.length"
          description="后端没有返回任何维度分数"
        />
        <template v-else>
          <el-alert
            v-if="allInsufficient"
            type="warning"
            :closable="false"
            title="三个维度都取不到分数：逐条列出的序列当前没有足够的已公布观测值（每个维度至少需要两个）。"
          />
          <el-alert
            v-else-if="!hasAnyScore"
            type="info"
            :closable="false"
            title="本次没有任何维度拿到分数。"
          />
          <div class="dimension-grid">
            <div v-for="item in dimensions" :key="item.code" class="dimension-card panel">
              <h3>{{ item.code === "growth" ? "增长" : item.code === "inflation" ? "通胀" : "流动性" }}</h3>
              <p class="score" :style="{ color: scoreView(item).tone }">{{ scoreView(item).label }}</p>
              <p class="muted">
                reason：<code>{{ item.reason ?? "（后端未返回）" }}</code>
                <template v-if="reasonView(item.reason)"> · {{ reasonView(item.reason) }}</template>
              </p>
              <p v-if="!item.evidence.length" class="muted">本次没有返回可用依据行。</p>
              <ul v-else class="evidence">
                <li v-for="line in item.evidence" :key="line">
                  {{ evidenceParts(line).headline }}
                  <span v-if="evidenceParts(line).published" class="muted">
                    （公布日 {{ evidenceParts(line).published }}）
                  </span>
                </li>
              </ul>
            </div>
          </div>
        </template>
      </section>

      <section class="panel">
        <h2>规则引用的序列</h2>
        <p class="muted">
          打分只用下面三条序列；它们在库里的真实观测值可以在
          <router-link to="/macro/liquidity">货币流动性</router-link> 与
          <router-link to="/macro/china">中国经济宏观</router-link> 页按同一观察日查看。
        </p>
        <el-table :data="RULE_SERIES" stripe>
          <el-table-column prop="dimension" label="维度" width="100" />
          <el-table-column prop="code" label="序列代码" width="140" />
          <el-table-column prop="name" label="名称" min-width="180" />
          <el-table-column prop="rule" label="规则口径" min-width="260" />
        </el-table>
      </section>
    </template>
  </main>
</template>

<style scoped>
.el-alert {
  margin-top: 12px;
}

.macro-alert {
  margin-top: 12px;
}

.macro-descriptions {
  margin: 8px 0 12px;
  --el-descriptions-table-border: 1px solid var(--line);
  --el-text-color-regular: var(--text);
  --el-fill-color-light: color-mix(in srgb, var(--text) 6%, transparent);
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin: 12px 0 8px;
}

.filter-date {
  width: 220px;
}

.dimension-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
  margin-top: 12px;
}

.dimension-card h3 {
  margin: 0 0 6px;
  font-size: 15px;
}

.score {
  margin: 0 0 6px;
  font-weight: 600;
}

.evidence {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--text);
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
