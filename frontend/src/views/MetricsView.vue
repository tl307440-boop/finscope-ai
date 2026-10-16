<script setup lang="ts">
/**
 * 财务指标试算（`POST /api/v1/metrics/evaluations`）。
 *
 * 后端不接一手财报：请求体里 q1/q2/market_cap/profit 全是调用方给的示例数字，
 * 返回的 quarters 也只按这些数字做减法，并带 notice「计算样例，不是公司真实财报」。
 * 这里把入参做成可编辑表单（默认值与原来写死在请求体里的完全一致），
 * 结果里每一格都优先用后端给的 display，缺值就写 reason（例如「负利润不计算市盈率」）。
 */
import { computed, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import MetricTile from "../components/ui/MetricTile.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
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
const peDisplay = ref<string | null>(null)
const peReason = ref<string | null>(null)

const scopeLabel = computed(() => (props.symbol ? `${props.symbol} · 计算样例` : "通用计算样例"))

const q2Row = computed(() => quarters.value.find((row) => row.quarter === "Q2") ?? null)
const q3Row = computed(() => quarters.value.find((row) => row.quarter === "Q3") ?? null)

function cellText(row: QuarterRow): string {
  return row.display ?? row.value ?? row.reason ?? "无法计算"
}

function qText(value: string): string {
  const trimmed = value.trim()
  return trimmed === "" ? "—（未填）" : trimmed
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
    peDisplay.value = asString(data?.pe_display)
    peReason.value = asString(data?.pe_reason)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="page">
    <PageHeader
      title="财务指标试算"
      :subtitle="`${scopeLabel} · 单季＝本期累计 − 上期累计；负利润时市盈率不计算`"
    >
      <template #actions>
        <StatusPill tone="warn" text="计算样例" />
      </template>
    </PageHeader>

    <div class="grid-1-2">
      <PanelCard title="输入" info="默认值与接口示例一致，可改；不落库、不关联真实财报">
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
          <button class="retry" type="submit" :disabled="loading">
            {{ loading ? "计算中…" : "计算样例" }}
          </button>
        </form>
      </PanelCard>

      <div class="stack">
        <PanelCard title="结论" :info="notice || '计算样例，不是公司真实财报。'">
          <StateBlock v-if="phase === 'idle'" state="empty" message="填好左侧数字后点「计算样例」。" />
          <template v-else-if="phase === 'error'">
            <StateBlock state="error" :message="failure" @retry="calculate" />
            <p v-if="requestId" class="muted request-line">请求号 {{ requestId }}</p>
          </template>
          <div v-else class="tiles">
            <MetricTile
              label="Q2 单季"
              :value="q2Row ? cellText(q2Row) : '后端未返回 Q2'"
              :hint="q2Row?.reason ?? '本期累计 − 上期累计'"
            />
            <MetricTile
              label="市盈率"
              :value="peDisplay ?? '无法计算'"
              :hint="peReason ?? '市值 ÷ 利润；利润为负时不给值'"
            />
            <MetricTile label="Q1 累计" :value="qText(q1)" hint="入参原样回读" />
            <MetricTile
              label="Q3 累计"
              :value="q3Row ? cellText(q3Row) : '未参与计算'"
              :hint="q3Row?.reason ?? '该季未填累计值，后端不拆分'"
            />
          </div>
        </PanelCard>

        <PanelCard title="输入回读" info="POST /api/v1/metrics/evaluations 的实际入参">
          <dl class="facts">
            <div>
              <dt>报表</dt>
              <dd>{{ STATEMENTS.find((item) => item.value === statement)?.label ?? statement }}</dd>
            </div>
            <div>
              <dt>Q2 累计</dt>
              <dd class="tabular">{{ qText(q2) }}</dd>
            </div>
            <div>
              <dt>市值</dt>
              <dd class="tabular">{{ qText(marketCap) }}</dd>
            </div>
            <div>
              <dt>利润</dt>
              <dd class="tabular">{{ qText(profit) }}</dd>
            </div>
          </dl>
          <p class="muted">
            接口按字符串收发金额，不做单位换算；这里的数字只参与本次试算，不会写入任何数据集。
          </p>
        </PanelCard>
      </div>
    </div>

    <PanelCard
      v-if="phase === 'ready'"
      title="单季拆解"
      info="Q1–Q4 全部由后端返回；缺值一列写的是后端给的 reason"
      flush
    >
      <el-table :data="quarters" size="small" stripe row-key="quarter">
        <el-table-column prop="quarter" label="季度" width="90" />
        <el-table-column label="单季值" width="180">
          <template #default="{ row }">
            <span class="tabular">{{ cellText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="累计值" width="160" class-name="tabular">
          <template #default="{ row }">
            <span class="tabular">{{ row.value ?? "—" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="后端说明" min-width="220">
          <template #default="{ row }">
            <span :class="row.reason ? '' : 'muted'">{{ row.reason ?? "—" }}</span>
          </template>
        </el-table-column>
      </el-table>
    </PanelCard>

    <SourceFooter
      :as-of="requestId ? `请求号 ${requestId}` : '—'"
      :sources="['POST /api/v1/metrics/evaluations', '用户输入（非财报数据）']"
      :note="notice || '计算样例，不是公司真实财报；本页不写入任何数据集'"
    />
  </main>
</template>

<style scoped>
.metrics-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.field {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

.field input,
.field select {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
}

.metrics-form .retry {
  grid-column: 1 / -1;
  justify-self: start;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
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

.request-line {
  margin: 8px 0 0;
  font-size: 12px;
}
</style>
