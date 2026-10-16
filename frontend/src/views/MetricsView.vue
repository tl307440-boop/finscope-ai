<script setup lang="ts">
/**
 * 财务指标试算（`POST /api/v1/metrics/evaluations`）。
 *
 * 后端不接一手财报：请求体里 q1/q2/market_cap/profit 全是调用方给的示例数字，
 * 返回的 quarters[].value 是按这些累计数做减法得到的单季值，并带 notice「计算样例，不是公司真实财报」。
 * 市盈率可计算时返回 pe_value，不适用时返回 pe_display/pe_reason。
 * 原因字段是英文枚举，这里统一映射成中文，不直接展示。
 */
import { computed, ref } from "vue"
import { DataAnalysis, EditPen, Grid } from "@element-plus/icons-vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import MetricTile from "../components/ui/MetricTile.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const props = defineProps<{ symbol?: string }>()

interface QuarterRow {
  quarter: string
  value: string | null
  display: string | null
  reason: string | null
}

const STATEMENTS = [
  { value: "income", label: "利润表" },
  { value: "cashflow", label: "现金流量表" },
  { value: "balance", label: "资产负债表" },
] as const

const REASON_TEXT: Record<string, string> = {
  insufficient_data: "缺少累计值，无法拆分",
  not_a_flow: "资产负债表是时点数，不拆单季",
  balance_is_point_in_time: "资产负债表是时点数，不拆单季",
  not_applicable: "利润为负或为零，市盈率不适用",
  sign_change: "由负转正",
  loss_widened: "亏损扩大",
}

const DISPLAY_TEXT: Record<string, string> = {
  "N/A": "不适用",
}

const phase = ref<"idle" | "ready" | "error">("idle")
const loading = ref(false)
const failure = ref("")
const requestId = ref("")

/** 默认值与改动前写死在请求体里的那组完全一致，保证后端口径不变。 */
const statement = ref<string>("income")
const q1 = ref("80")
const q2 = ref("200")
const q3 = ref("")
const fy = ref("")
const marketCap = ref("100")
const profit = ref("-5")

const notice = ref("")
const quarters = ref<QuarterRow[]>([])
const peValue = ref<string | null>(null)
const peDisplay = ref<string | null>(null)
const peReason = ref<string | null>(null)

const subtitle = computed(() =>
  props.symbol
    ? `${props.symbol} · 用累计数拆单季、算市盈率；数字为手动输入的样例`
    : "用累计数拆单季、算市盈率；数字为手动输入的样例",
)

const q2Row = computed(() => quarters.value.find((row) => row.quarter === "Q2") ?? null)
const q3Row = computed(() => quarters.value.find((row) => row.quarter === "Q3") ?? null)

const cumulativeByQuarter = computed<Record<string, string>>(() => ({
  Q1: q1.value.trim(),
  Q2: q2.value.trim(),
  Q3: q3.value.trim(),
  Q4: fy.value.trim(),
}))

function reasonText(reason: string | null): string | null {
  if (!reason) {
    return null
  }
  return REASON_TEXT[reason] ?? "无法计算"
}

function displayText(display: string | null): string | null {
  if (!display) {
    return null
  }
  return DISPLAY_TEXT[display] ?? display
}

function cellText(row: QuarterRow): string {
  return row.value ?? displayText(row.display) ?? (row.reason ? "无法计算" : "—")
}

function noteText(row: QuarterRow): string {
  if (row.value !== null) {
    return row.quarter === "Q1" ? "Q1 单季即 Q1 累计" : "本期累计 − 上期累计"
  }
  return reasonText(row.reason) ?? "—"
}

const peText = computed<string>(() => {
  if (peValue.value !== null) {
    const parsed = Number.parseFloat(peValue.value)
    return Number.isFinite(parsed) ? parsed.toFixed(2) : peValue.value
  }
  return displayText(peDisplay.value) ?? "无法计算"
})

const peNote = computed<string>(() => {
  if (peValue.value !== null) {
    return "市值 ÷ 利润"
  }
  return reasonText(peReason.value) ?? "需同时填写市值与利润"
})

function qText(value: string): string {
  const trimmed = value.trim()
  return trimmed === "" ? "未填" : trimmed
}

async function calculate(): Promise<void> {
  loading.value = true
  failure.value = ""
  requestId.value = ""
  try {
    const payload: Record<string, string> = {
      statement: statement.value,
      q1: q1.value.trim(),
      q2: q2.value.trim(),
      market_cap: marketCap.value.trim(),
      profit: profit.value.trim(),
    }
    if (q3.value.trim()) {
      payload.q3 = q3.value.trim()
    }
    if (fy.value.trim()) {
      payload.fy = fy.value.trim()
    }
    const response = await fetch(apiUrl("/api/v1/metrics/evaluations"), {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    requestId.value = asString(asRecord(asRecord(body)?.meta)?.request_id) ?? ""
    notice.value = asString(data?.notice) ?? "计算样例，不是公司真实财报。"
    const raw = Array.isArray(data?.quarters) ? data.quarters : []
    quarters.value = raw.flatMap((item) => {
      const row = asRecord(item)
      const name = asString(row?.quarter)
      if (!row || !name) {
        return []
      }
      return [
        {
          quarter: name,
          value: asString(row.value),
          display: asString(row.display),
          reason: asString(row.reason),
        },
      ]
    })
    peValue.value = asString(data?.pe_value)
    peDisplay.value = asString(data?.pe_display)
    peReason.value = asString(data?.pe_reason)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="page">
    <PageHeader title="财务指标试算" :subtitle="subtitle">
      <template #actions>
        <StatusPill tone="warn" text="计算样例 · 非公司财报" />
      </template>
    </PageHeader>

    <div class="stack">
      <div class="grid-1-2">
        <PanelCard title="输入" info="填写累计数与市值、利润；不保存，也不关联公司真实财报">
          <template #icon><el-icon><EditPen /></el-icon></template>
          <form class="metrics-form" @submit.prevent="calculate">
            <label class="field">
              报表
              <select v-model="statement" name="metrics-statement">
                <option v-for="item in STATEMENTS" :key="item.value" :value="item.value">
                  {{ item.label }}
                </option>
              </select>
            </label>
            <label class="field">
              Q1 累计
              <input v-model="q1" name="metrics-q1" inputmode="decimal" placeholder="80" />
            </label>
            <label class="field">
              Q2 累计
              <input v-model="q2" name="metrics-q2" inputmode="decimal" placeholder="200" />
            </label>
            <label class="field">
              Q3 累计（可空）
              <input v-model="q3" name="metrics-q3" inputmode="decimal" placeholder="留空则不出 Q3/Q4" />
            </label>
            <label class="field">
              全年累计（可空）
              <input v-model="fy" name="metrics-fy" inputmode="decimal" placeholder="留空则不出 Q4" />
            </label>
            <label class="field">
              市值
              <input v-model="marketCap" name="metrics-market-cap" inputmode="decimal" placeholder="100" />
            </label>
            <label class="field">
              利润
              <input v-model="profit" name="metrics-profit" inputmode="decimal" placeholder="-5" />
            </label>
            <el-button class="submit" type="primary" native-type="submit" :loading="loading">计算样例</el-button>
          </form>
        </PanelCard>

        <PanelCard title="结论" :info="notice || '计算样例，不是公司真实财报。'">
          <template #icon><el-icon><DataAnalysis /></el-icon></template>
          <p v-if="phase === 'idle'" class="muted idle-line">填好左侧数字后点「计算样例」，结果会显示在这里。</p>
          <template v-else-if="phase === 'error'">
            <StateBlock state="error" :message="failure" @retry="calculate" />
            <p v-if="requestId" class="muted request-line">请求编号 {{ requestId }}</p>
          </template>
          <div v-else class="tiles">
            <MetricTile
              label="Q2 单季"
              :value="q2Row ? cellText(q2Row) : '未返回'"
              :delta-label="q2Row ? noteText(q2Row) : '本期累计 − 上期累计'"
            />
            <MetricTile label="市盈率" :value="peText" :delta-label="peNote" />
            <MetricTile
              label="Q3 单季"
              :value="q3Row ? cellText(q3Row) : '未参与计算'"
              :delta-label="q3Row ? noteText(q3Row) : '未填 Q3 累计值，不拆分'"
            />
            <MetricTile
              label="报表"
              :value="STATEMENTS.find((item) => item.value === statement)?.label ?? '—'"
              :delta-label="`市值 ${qText(marketCap)} · 利润 ${qText(profit)}`"
            />
          </div>
        </PanelCard>
      </div>

      <PanelCard
        v-if="phase === 'ready'"
        title="单季拆解"
        info="单季值 = 本期累计 − 上期累计；无法计算时给出原因"
        flush
      >
        <template #icon><el-icon><Grid /></el-icon></template>
        <el-table :data="quarters" size="small" row-key="quarter">
          <el-table-column prop="quarter" label="季度" width="90" />
          <el-table-column label="累计值（输入）" width="160" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ qText(cumulativeByQuarter[row.quarter] ?? "") }}</span>
            </template>
          </el-table-column>
          <el-table-column label="单季值" width="160" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ cellText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="220">
            <template #default="{ row }">
              <span :class="row.value === null ? 'warn-text' : 'muted'">{{ noteText(row) }}</span>
            </template>
          </el-table-column>
        </el-table>
        <p class="table-note">{{ notice || "计算样例，不是公司真实财报。" }} 金额按输入原样计算，不做单位换算，结果不会保存。</p>
      </PanelCard>
    </div>
  </main>
</template>

<style scoped>
.metrics-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.field {
  display: grid;
  gap: 4px;
  font-size: 13px;
  color: var(--muted);
}

.field input,
.field select {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-deep);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
}

.field input {
  font-family: var(--font-num);
}

.field input:focus,
.field select:focus {
  outline: none;
  border-color: var(--accent);
}

.metrics-form .submit {
  grid-column: 1 / -1;
  justify-self: start;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.idle-line {
  margin: 0;
  font-size: 13px;
}

.request-line {
  margin: 8px 0 0;
  font-size: 13px;
}

.warn-text {
  color: var(--warn);
}

.table-note {
  margin: 10px 0 4px;
  font-size: 13px;
  color: var(--muted);
}
</style>
