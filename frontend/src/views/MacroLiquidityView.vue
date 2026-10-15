<script setup lang="ts">
/**
 * 货币流动性（/macro/liquidity）。
 *
 * 真实接口（已用 admin 令牌逐一打过；字段以 backend/app/schemas/macro.py、
 * backend/app/api/v1/macro.py 与 backend/app/catalog/macro_series.py 为准）：
 *  - GET /api/v1/macro/series   （无查询参数，后端不接受 region/category/enabled）
 *      data = { provider_code: "fred", availability: "unavailable"|"configured",
 *               notice, series[] }
 *      series[] = { series_code, name_cn, name_en, category, frequency,
 *                   base_unit, seasonal_adjustment }
 *      category 实测取值：rates / output / labor / inflation
 *  - GET /api/v1/macro/series/{series_code}/observations?start=&end=&as_of=&limit=
 *      data = { series_code, pit_date, time_precision, observations[],
 *               empty_message, truncated }
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value,
 *               reason, evidence[] }, notice, version }
 *      value 缺省表示规则取不到两个已公布观测值（实测：growth/liquidity 为
 *      value=null、reason="insufficient_data"）。
 *
 * 口径说明（如实反映后端，不硬凑概念）：
 *  - 后端序列目录里**没有**「货币供应量 / 流动性总量」这一分类，唯一与资金价格
 *    直接相关的是 category === "rates"（实测 4 条）。本页用它作为流动性的观测
 *    对象，并明确标注这只是利率类，不代表货币供应量。
 *  - **后端没有 POST /api/v1/macro/assessments**（唯一的 POST 是仅管理员的
 *    /macro/china/observations 导入），本页只读展示，分数与理由原样转发。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import { describeObservations, type MacroObservation } from "../api/macro"

type Phase = "loading" | "ready" | "error"

interface SeriesRow {
  code: string
  name: string
  nameEn: string
  category: string
  frequency: string
  unit: string
  seasonalAdjustment: string
}

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

const phase = ref<Phase>("loading")
const failure = ref("")
const providerCode = ref("")
const availability = ref("")
const catalogNotice = ref("")
const series = ref<SeriesRow[]>([])
const assessment = ref<Assessment | null>(null)
const assessmentFailed = ref(false)
const selectedCode = ref("")
const asOf = ref("")

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<MacroObservation[]>([])
const pitDate = ref("")
const emptyMessage = ref("")
const truncated = ref(false)

/** 后端自己给出的分类 "rates"：与资金价格直接相关的序列。 */
const rateSeries = computed<SeriesRow[]>(() => series.value.filter((row) => row.category === "rates"))

const otherSeries = computed<SeriesRow[]>(() => series.value.filter((row) => row.category !== "rates"))

const categoryLegend = computed<string>(() => {
  const counted = new Map<string, number>()
  for (const row of series.value) {
    counted.set(row.category, (counted.get(row.category) ?? 0) + 1)
  }
  return [...counted.entries()].map(([category, count]) => `${category} ${count}`).join(" · ")
})

const availabilityLabel = computed<string>(() => {
  if (availability.value === "configured") {
    return "外部数据源已配置"
  }
  if (availability.value === "unavailable") {
    return "外部数据源未配置（序列定义仍在，观测值需同步或导入）"
  }
  return availability.value || "—"
})

const selectedRow = computed<SeriesRow | null>(
  () => series.value.find((row) => row.code === selectedCode.value) ?? null,
)

const chartOption = computed<EChartsCoreOption | null>(() => buildTrendOption(observations.value))

const observationLines = computed<string[]>(() =>
  describeObservations({ emptyMessage: emptyMessage.value || null, observations: observations.value }),
)

/**
 * 后端只回分数不回文案；分数含义由 backend/app/services/macro_rules.py 决定：
 * 70=该序列下行（流动性维度 tighter_when_rising，故为偏松），30=上行（偏紧），
 * 50=持平，value 缺省=不足两个已公布观测值。
 * 分数不是价格，所以这里用正文色/弱化色，涨红跌绿只用在下面走势图的价格方向上。
 */
function scoreView(score: DimensionScore | null): { label: string; tone: string } {
  if (!score || score.value === null) {
    return { label: "数据不足", tone: "var(--muted)" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value === 70) {
    return {
      label: liquidity ? "偏松（利率下行，70）" : "下行（70）",
      tone: "var(--text)",
    }
  }
  if (score.value === 30) {
    return {
      label: liquidity ? "偏紧（利率上行，30）" : "上行（30）",
      tone: "var(--text)",
    }
  }
  if (score.value === 50) {
    return { label: "持平（50）", tone: "var(--text)" }
  }
  return { label: `分数 ${score.value}`, tone: "var(--text)" }
}

/** 后端 reason 是枚举值，这里只做展示翻译；未收录的取值原样显示。 */
function reasonView(reason: string | null): string {
  if (!reason) {
    return ""
  }
  if (reason === "insufficient_data") {
    return "不足两个已公布观测值，规则无法给出方向"
  }
  return reason
}

function parseSeries(value: unknown): SeriesRow[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.series_code)
    const name = asString(item.name_cn)
    if (!code || !name) {
      return []
    }
    return [
      {
        code,
        name,
        nameEn: asString(item.name_en) ?? "",
        category: asString(item.category) ?? "—",
        frequency: asString(item.frequency) ?? "—",
        unit: asString(item.base_unit) ?? "—",
        seasonalAdjustment: asString(item.seasonal_adjustment) ?? "—",
      },
    ]
  })
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

function parseObservation(value: unknown): MacroObservation[] {
  const record = asRecord(value)
  const observationDate = asString(record?.observation_date)
  const amount = asString(record?.value)
  const unit = asString(record?.unit)
  const realtimeStart = asString(record?.realtime_start)
  const revision = asNumber(record?.revision_no)
  if (!observationDate || amount === null || !unit || !realtimeStart || revision === null) {
    return []
  }
  return [
    {
      observation_date: observationDate,
      value: amount,
      unit,
      realtime_start: realtimeStart,
      revision_no: revision,
    },
  ]
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  assessmentFailed.value = false
  try {
    const [seriesResponse, assessmentResponse] = await Promise.all([
      fetch(apiUrl("/api/v1/macro/series")),
      fetch(apiUrl("/api/v1/macro/assessments")),
    ])
    const seriesBody = await readJson(seriesResponse)
    if (!seriesResponse.ok) {
      throw errorFrom(seriesBody, seriesResponse.headers.get("X-Request-ID"))
    }
    const seriesData = asRecord(asRecord(seriesBody)?.data)
    providerCode.value = asString(seriesData?.provider_code) ?? ""
    availability.value = asString(seriesData?.availability) ?? ""
    catalogNotice.value = asString(seriesData?.notice) ?? ""
    series.value = parseSeries(seriesData?.series)

    // 评估接口失败不让整页失败，但要如实标注「未取到」，不编造结论。
    assessment.value = null
    if (assessmentResponse.ok) {
      const assessmentBody = await readJson(assessmentResponse)
      const data = asRecord(asRecord(assessmentBody)?.data)
      assessment.value = {
        pitDate: asString(data?.pit_date) ?? "",
        regimeCode: asString(data?.regime_code) ?? "",
        growth: parseScore(data?.growth),
        inflation: parseScore(data?.inflation),
        liquidity: parseScore(data?.liquidity),
        notice: asString(data?.notice) ?? "",
        version: asString(data?.version) ?? "",
      }
    } else {
      assessmentFailed.value = true
    }

    if (!selectedCode.value) {
      selectedCode.value = (rateSeries.value[0] ?? series.value[0])?.code ?? ""
    }
    phase.value = "ready"
    if (selectedCode.value) {
      await loadObservations()
    }
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadObservations(): Promise<void> {
  const code = selectedCode.value
  if (!code) {
    detailPhase.value = "idle"
    observations.value = []
    return
  }
  detailPhase.value = "loading"
  detailError.value = ""
  observations.value = []
  pitDate.value = ""
  emptyMessage.value = ""
  truncated.value = false
  try {
    const params = new URLSearchParams({ limit: "500" })
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const response = await fetch(
      apiUrl(`/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    observations.value = Array.isArray(data?.observations)
      ? data.observations.flatMap(parseObservation)
      : []
    pitDate.value = asString(data?.pit_date) ?? ""
    emptyMessage.value = asString(data?.empty_message) ?? ""
    truncated.value = data?.truncated === true
    detailPhase.value = "ready"
  } catch (caught) {
    detailError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    detailPhase.value = "error"
  }
}

function selectSeries(code: string): void {
  selectedCode.value = code
  void loadObservations()
}

function trendColor(values: number[]): string {
  if (values.length < 2) {
    return "var(--accent)"
  }
  const last = values[values.length - 1]
  const previous = values[values.length - 2]
  if (last === undefined || previous === undefined || last === previous) {
    return "var(--accent)"
  }
  return last > previous ? "var(--rise)" : "var(--fall)"
}

function buildTrendOption(rows: MacroObservation[]): EChartsCoreOption | null {
  const points = rows.flatMap((row) => {
    const parsed = asNumber(row.value)
    return parsed === null ? [] : [{ date: row.observation_date, value: parsed, raw: row.value }]
  })
  if (points.length < 2) {
    return null
  }
  const values = points.map((point) => point.value)
  const color = trendColor(values)
  const zoom = points.length > 40
  return {
    grid: { left: 8, right: 16, top: 24, bottom: zoom ? 48 : 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
      formatter: (params: unknown): string => {
        const first = Array.isArray(params) ? asRecord(params[0]) : asRecord(params)
        const index = asNumber(first?.dataIndex)
        const point = index === null ? undefined : points[index]
        if (!point) {
          return ""
        }
        return `${point.date} · ${point.raw}`
      },
    },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      axisLabel: { color: "var(--muted)", hideOverlap: true },
      axisLine: { lineStyle: { color: "var(--line)" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "var(--muted)" },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        type: "line",
        name: selectedCode.value,
        data: values,
        showSymbol: points.length <= 60,
        smooth: false,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        areaStyle: { opacity: 0.08, color },
      },
    ],
    ...(zoom
      ? {
          dataZoom: [
            { type: "inside", start: 60, end: 100 },
            { type: "slider", start: 60, end: 100, height: 18, bottom: 8 },
          ],
        }
      : {}),
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取货币流动性数据</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取货币流动性数据</h1>
      <p class="muted">{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <section class="panel">
        <h1>货币流动性</h1>
        <p class="muted">
          序列目录共 {{ series.length }} 条（数据源 {{ providerCode || "—" }}，{{ availabilityLabel }}），
          按后端分类：{{ categoryLegend || "—" }}。
        </p>
        <el-alert type="info" :closable="false" title="口径说明（不硬凑概念）">
          <template #default>
            后端序列目录里没有「货币供应量 / 流动性总量」这一独立分类，唯一与资金价格直接相关的分类是
            <strong>rates（利率类，共 {{ rateSeries.length }} 条）</strong>。本页把利率类序列作为流动性的
            观测对象，只展示后端真实存在的分类与序列，不把利率冒充货币供应量。
          </template>
        </el-alert>
        <el-alert v-if="catalogNotice" type="info" :closable="false" class="macro-alert" title="目录说明">
          <template #default>{{ catalogNotice }}</template>
        </el-alert>
      </section>

      <section class="panel">
        <h2>规则口径的流动性判断</h2>
        <p v-if="assessmentFailed" class="muted" role="alert">
          评估接口本次未返回可用结果，本页不编造结论。
          <button class="retry" type="button" @click="load">重试</button>
        </p>
        <p v-else-if="!assessment" class="muted">未取到评估结果，本页不编造结论。</p>
        <template v-else>
          <p class="muted">
            后端时点 {{ assessment.pitDate || "—" }} · 组合状态
            <code>{{ assessment.regimeCode || "—" }}</code>
            <template v-if="assessment.version"> · 规则版本 {{ assessment.version }}</template>
          </p>
          <el-descriptions :column="1" border class="macro-descriptions">
            <el-descriptions-item label="流动性维度">
              <span class="score" :style="{ color: scoreView(assessment.liquidity).tone }">
                {{ scoreView(assessment.liquidity).label }}
              </span>
              <span v-if="assessment.liquidity" class="muted">
                · {{ reasonView(assessment.liquidity.reason) }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="增长维度">
              <span class="score" :style="{ color: scoreView(assessment.growth).tone }">
                {{ scoreView(assessment.growth).label }}
              </span>
              <span v-if="assessment.growth" class="muted">
                · {{ reasonView(assessment.growth.reason) }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item label="通胀维度">
              <span class="score" :style="{ color: scoreView(assessment.inflation).tone }">
                {{ scoreView(assessment.inflation).label }}
              </span>
              <span v-if="assessment.inflation" class="muted">
                · {{ reasonView(assessment.inflation.reason) }}
              </span>
            </el-descriptions-item>
            <el-descriptions-item
              v-if="assessment.liquidity && assessment.liquidity.evidence.length"
              label="流动性判定依据"
            >
              <ul class="evidence">
                <li v-for="line in assessment.liquidity.evidence" :key="line">{{ line }}</li>
              </ul>
              <p class="muted">依据行格式为「序列代码 观测日 数值 公布日」，用于核对时点可见性。</p>
            </el-descriptions-item>
          </el-descriptions>
          <el-alert v-if="assessment.notice" type="warning" :closable="false" class="macro-alert">
            <template #default>{{ assessment.notice }}</template>
          </el-alert>
          <p class="muted">
            分数口径：联邦基金利率（FEDFUNDS）上行＝30 偏紧、下行＝70 偏松、持平＝50；分数与理由全部来自后端，
            本页只做文案翻译，不改口也不外推。
          </p>
        </template>
      </section>

      <section class="panel">
        <h2>利率类序列（category = rates，{{ rateSeries.length }} 条）</h2>
        <el-empty v-if="!rateSeries.length" description="后端没有返回任何利率类序列" />
        <el-table v-else :data="rateSeries" stripe @row-click="(row: SeriesRow) => selectSeries(row.code)">
          <el-table-column prop="code" label="代码" width="130" />
          <el-table-column prop="name" label="名称" min-width="180" />
          <el-table-column prop="unit" label="单位" min-width="150" />
          <el-table-column prop="frequency" label="频率" width="110" />
          <el-table-column prop="seasonalAdjustment" label="季调" min-width="180" />
          <el-table-column label="操作" width="110">
            <template #default="scope">
              <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                查看走势
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="panel">
        <h2>序列走势</h2>
        <div class="filter-row">
          <el-select v-model="selectedCode" class="filter-select" @change="selectSeries">
            <el-option
              v-for="item in series"
              :key="item.code"
              :value="item.code"
              :label="`${item.code} ${item.name}`"
            />
          </el-select>
          <el-date-picker
            v-model="asOf"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="观察日（留空＝今天）"
            class="filter-date"
          />
          <el-button type="primary" :loading="detailPhase === 'loading'" @click="loadObservations">
            查询
          </el-button>
        </div>
        <p class="muted">
          观察日按后端时点过滤：只显示当天已经公布的观测值（realtime_start 早于观察日）。留空表示截止今天。
        </p>

        <div v-if="detailPhase === 'loading'" class="skeleton" aria-busy="true">正在查询观测值</div>

        <template v-else-if="detailPhase === 'error'">
          <p role="alert" class="macro-error">{{ detailError }}</p>
          <button class="retry" type="button" @click="loadObservations">重试</button>
        </template>

        <template v-else-if="detailPhase === 'ready'">
          <el-descriptions v-if="selectedRow" :column="3" border class="macro-descriptions">
            <el-descriptions-item label="序列">{{ selectedRow.code }}</el-descriptions-item>
            <el-descriptions-item label="名称">{{ selectedRow.name }}</el-descriptions-item>
            <el-descriptions-item label="后端时点">{{ pitDate || "—" }}</el-descriptions-item>
          </el-descriptions>

          <el-alert
            v-if="truncated"
            type="info"
            :closable="false"
            class="macro-alert"
            title="观测值多于本次上限，图表只画出了返回的部分。"
          />

          <VChart v-if="chartOption" class="macro-chart" :option="chartOption" autoresize />
          <p v-else class="muted">少于两个可解析的观测值，暂不画走势图。</p>

          <el-empty v-if="!observations.length" :description="emptyMessage || '该观察日没有可见的观测值'" />

          <template v-else>
            <p class="muted">共 {{ observations.length }} 条观测值（按后端返回顺序）。</p>
            <ul class="macro-lines">
              <li v-for="line in observationLines" :key="line">{{ line }}</li>
            </ul>
          </template>
        </template>
      </section>

      <section class="panel">
        <h2>其余序列（{{ otherSeries.length }} 条，非利率类）</h2>
        <p class="muted">
          以下序列与资金价格无关，一并列出便于核对后端目录；它们不属于流动性口径，不参与本页判断。
        </p>
        <el-empty v-if="!otherSeries.length" description="没有其他分类的序列" />
        <el-table v-else :data="otherSeries" stripe @row-click="(row: SeriesRow) => selectSeries(row.code)">
          <el-table-column prop="code" label="代码" width="130" />
          <el-table-column prop="name" label="名称" min-width="180" />
          <el-table-column prop="category" label="分类" width="110" />
          <el-table-column prop="unit" label="单位" min-width="150" />
          <el-table-column prop="frequency" label="频率" width="110" />
          <el-table-column prop="seasonalAdjustment" label="季调" min-width="180" />
          <el-table-column label="操作" width="110">
            <template #default="scope">
              <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                查看走势
              </el-button>
            </template>
          </el-table-column>
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

.macro-error {
  color: var(--danger);
}

.macro-descriptions {
  margin: 8px 0 12px;
  --el-descriptions-table-border: 1px solid var(--line);
  --el-text-color-regular: var(--text);
  --el-fill-color-light: color-mix(in srgb, var(--text) 6%, transparent);
}

.evidence {
  margin: 0;
  padding-left: 18px;
}

.score {
  font-weight: 600;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.filter-select {
  width: min(360px, 100%);
}

.filter-date {
  width: 220px;
}

.macro-chart {
  height: 300px;
  margin-top: 12px;
}

.macro-lines {
  margin: 8px 0 0;
  padding-left: 20px;
  color: var(--muted);
  font-size: 13px;
}

.el-table {
  margin-top: 8px;
  cursor: pointer;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
