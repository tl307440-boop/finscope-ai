/**
 * 多角色研究推演接口（后端 app/api/v1/simulation.py）。
 * 后端用 exclude_none 序列化，缺失字段一律按「没有」处理，不在前端补数。
 */
import { authorizedFetch, errorFrom, readJson } from "./client"
import { asBool, asList, asNumber, asRecord, asString, dataOf, listOf } from "./json"

export type Stance = "bullish" | "bearish" | "neutral" | "unavailable"
export type RiskProfile = "aggressive" | "neutral" | "conservative"

export interface CitationRef {
  source: string
  label: string
  ref: string
  observedAt: string
}

export interface FindingItem {
  text: string
  polarity: -1 | 0 | 1
  weight: number
  severe: boolean
  citations: CitationRef[]
}

export interface AnalystItem {
  analyst: string
  role: string
  coverage: "full" | "partial" | "unavailable"
  stance: Stance
  score: number | null
  summary: string
  findings: FindingItem[]
  gaps: string[]
}

export interface DebateItem {
  round: number
  bull: string
  bear: string
  bullCitations: CitationRef[]
  bearCitations: CitationRef[]
  arbitration: string
  unsupported: string[]
  converged: boolean
}

export interface ProposalItem {
  rating: string
  displayRating: string
  composite: string | null
  debateTilt: string
  allocation: string
  horizonDays: number | null
  stopLoss: string
  revision: number
  rationale: string
}

export interface RiskItem {
  profile: RiskProfile
  approved: boolean | null
  content: string
  vetoReason: string | null
  scenario: string | null
  drawdown: number | null
  mitigations: string[]
  revision: number
}

export interface MemoryItem {
  asOf: string
  rating: string
  excessReturn: number | null
  verdict: string
  reflection: string
}

export interface EvaluationItem {
  qualityFlag: string
  actualReturn: number | null
  benchmarkSymbol: string | null
  benchmarkReturn: number | null
  excessReturn: number | null
  verdict: string | null
  reflection: string | null
  holdingDays: number | null
  exitDate: string | null
  note: string
}

export interface SimulationRun {
  runId: string
  status: string
  symbol: string
  asOf: string
  rating: string
  decision: string
  engine: string
  engineLabel: string
  llm: boolean
  holdingDays: number | null
  maxDebateRounds: number | null
  durationMs: number | null
  createdAt: string
  subjectName: string
  industry: string | null
  market: string
  analysts: AnalystItem[]
  debates: DebateItem[]
  memo: string
  proposals: ProposalItem[]
  risks: RiskItem[]
  confidence: number | null
  allocation: string | null
  horizon: string | null
  stopLoss: string | null
  drivers: FindingItem[]
  riskFactors: FindingItem[]
  aborted: boolean
  memory: MemoryItem[]
  evaluation: EvaluationItem | null
  disclaimer: string
}

export interface RunSummary {
  runId: string
  symbol: string
  asOf: string
  status: string
  rating: string
  confidence: number | null
  engine: string
  createdAt: string
  excessReturn: number | null
  verdict: string | null
}

export interface ExperimentMode {
  code: string
  label: string
  samples: number
  directionalCalls: number
  hitRate: number | null
  longMeanExcess: number | null
  shortMeanExcess: number | null
  longShortSpread: number | null
  meanSignedExcess: number | null
}

export interface ExperimentRatingRow {
  rating: string
  count: number
  meanExcess: number | null
  baselineCount: number
  baselineMeanExcess: number | null
}

export interface ExperimentResult {
  samples: number
  skippedCount: number
  skipped: string[]
  truncated: boolean
  modes: ExperimentMode[]
  byRating: ExperimentRatingRow[]
  rows: { symbol: string; asOf: string; rating: string; baseline: string; excess: number | null }[]
}

export const RATING_LABEL: Record<string, string> = {
  Buy: "买入",
  Overweight: "增持",
  Hold: "持有观望",
  Underweight: "减持",
  Sell: "卖出",
  REVIEW: "无评级",
}

export const VERDICT_LABEL: Record<string, string> = {
  accurate: "方向一致",
  partially_accurate: "大体持平",
  divergent: "方向相反",
}

export const PROFILE_LABEL: Record<RiskProfile, string> = {
  aggressive: "激进风控",
  neutral: "中性风控",
  conservative: "保守风控",
}

const num = (value: unknown): number | null => {
  const direct = asNumber(value)
  if (direct !== null) {
    return direct
  }
  const text = asString(value)
  if (text === null || text.trim() === "") {
    return null
  }
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : null
}

/** 旧记录里评级存成小写内部码（hold），统一成展示码（Hold）。 */
const displayRating = (value: string): string =>
  value === value.toLowerCase() && value ? value[0].toUpperCase() + value.slice(1) : value

const texts = (value: unknown): string[] =>
  asList(value).filter((item): item is string => typeof item === "string")

function parseCitations(value: unknown): CitationRef[] {
  return listOf(value).map((item) => ({
    source: asString(item.source) ?? "",
    label: asString(item.label) ?? "",
    ref: asString(item.ref) ?? "",
    observedAt: asString(item.observed_at) ?? "",
  }))
}

function parseFindings(value: unknown): FindingItem[] {
  return listOf(value).map((item) => {
    const polarity = num(item.polarity)
    return {
      text: asString(item.text) ?? "",
      polarity: polarity === 1 ? 1 : polarity === -1 ? -1 : 0,
      weight: num(item.weight) ?? 1,
      severe: asBool(item.severe) ?? false,
      citations: parseCitations(item.citations),
    }
  })
}

function parseAnalyst(item: Record<string, unknown>): AnalystItem {
  const coverage = asString(item.coverage)
  const stance = asString(item.stance)
  return {
    analyst: asString(item.analyst) ?? "",
    role: asString(item.role) ?? "",
    coverage: coverage === "full" || coverage === "partial" ? coverage : "unavailable",
    stance:
      stance === "bullish" || stance === "bearish" || stance === "neutral" ? stance : "unavailable",
    score: num(item.score),
    summary: asString(item.summary) ?? "",
    findings: parseFindings(item.findings),
    gaps: texts(item.gaps),
  }
}

function parseProfile(value: unknown): RiskProfile {
  return value === "aggressive" || value === "conservative" ? value : "neutral"
}

export function parseRun(body: unknown): SimulationRun | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  const config = asRecord(data.config) ?? {}
  const detail = asRecord(data.detail) ?? {}
  const subject = asRecord(detail.subject) ?? {}
  const decision = asRecord(detail.decision) ?? {}
  const evaluation = asRecord(detail.evaluation)
  return {
    runId: asString(data.run_id) ?? "",
    status: asString(data.status) ?? "",
    symbol: asString(data.symbol) ?? "",
    asOf: asString(data.as_of) ?? "",
    rating: asString(data.rating) ?? "REVIEW",
    decision: asString(data.decision) ?? "",
    engine: asString(data.engine) ?? "",
    engineLabel: asString(data.engine_label) ?? "",
    llm: asBool(config.llm) ?? false,
    holdingDays: num(config.holding_days),
    maxDebateRounds: num(config.max_debate_rounds),
    durationMs: num(data.duration_ms),
    createdAt: asString(data.created_at) ?? "",
    subjectName: asString(subject.name) ?? "",
    industry: asString(subject.industry),
    market: asString(subject.market) ?? "",
    analysts: listOf(detail.analysts).map(parseAnalyst),
    debates: listOf(detail.debates).map((item) => ({
      round: num(item.round) ?? 0,
      bull: asString(item.bull) ?? "",
      bear: asString(item.bear) ?? "",
      bullCitations: parseCitations(item.bull_citations),
      bearCitations: parseCitations(item.bear_citations),
      arbitration: asString(item.arbitration) ?? "",
      unsupported: texts(item.unsupported),
      converged: asBool(item.converged) ?? false,
    })),
    memo: asString(detail.memo) ?? "",
    proposals: listOf(detail.proposals).map((item) => ({
      rating: asString(item.rating) ?? "",
      displayRating: asString(item.display_rating) ?? "",
      composite: asString(item.composite),
      debateTilt: asString(item.debate_tilt) ?? "0",
      allocation: asString(item.allocation) ?? "",
      horizonDays: num(item.horizon_days),
      stopLoss: asString(item.stop_loss) ?? "",
      revision: num(item.revision) ?? 0,
      rationale: asString(item.rationale) ?? "",
    })),
    risks: listOf(detail.risks).map((item) => ({
      profile: parseProfile(item.profile),
      approved: asBool(item.approved),
      content: asString(item.content) ?? "",
      vetoReason: asString(item.veto_reason),
      scenario: asString(item.scenario),
      drawdown: num(item.drawdown),
      mitigations: texts(item.mitigations),
      revision: num(item.revision) ?? 0,
    })),
    confidence: num(decision.confidence),
    allocation: asString(decision.allocation),
    horizon: asString(decision.horizon),
    stopLoss: asString(decision.stop_loss),
    drivers: parseFindings(decision.drivers),
    riskFactors: parseFindings(decision.risks),
    aborted: asBool(decision.aborted) ?? false,
    memory: listOf(detail.memory).map((item) => ({
      asOf: asString(item.as_of) ?? "",
      rating: displayRating(asString(item.rating) ?? ""),
      excessReturn: num(item.excess_return),
      verdict: asString(item.verdict) ?? "",
      reflection: asString(item.reflection) ?? "",
    })),
    evaluation: evaluation ? parseEvaluation(evaluation) : null,
    disclaimer: asString(data.disclaimer) ?? "",
  }
}

export function parseEvaluation(record: Record<string, unknown>): EvaluationItem {
  return {
    qualityFlag: asString(record.quality_flag) ?? "insufficient",
    actualReturn: num(record.actual_return),
    benchmarkSymbol: asString(record.benchmark_symbol),
    benchmarkReturn: num(record.benchmark_return),
    excessReturn: num(record.excess_return),
    verdict: asString(record.verdict),
    reflection: asString(record.reflection),
    holdingDays: num(record.holding_days),
    exitDate: asString(record.exit_date),
    note: asString(record.note) ?? "",
  }
}

export function parseRunList(body: unknown): RunSummary[] {
  return listOf(dataOf(body)?.runs).map((item) => ({
    runId: asString(item.run_id) ?? "",
    symbol: asString(item.symbol) ?? "",
    asOf: asString(item.as_of) ?? "",
    status: asString(item.status) ?? "",
    rating: asString(item.rating) ?? "REVIEW",
    confidence: num(item.confidence),
    engine: asString(item.engine) ?? "",
    createdAt: asString(item.created_at) ?? "",
    excessReturn: num(item.excess_return),
    verdict: asString(item.verdict),
  }))
}

export function parseExperiment(body: unknown): ExperimentResult | null {
  const data = dataOf(body)
  if (!data) {
    return null
  }
  return {
    samples: num(data.samples) ?? 0,
    skippedCount: num(data.skipped_count) ?? 0,
    skipped: texts(data.skipped),
    truncated: asBool(data.truncated) ?? false,
    modes: listOf(data.modes).map((item) => ({
      code: asString(item.code) ?? "",
      label: asString(item.label) ?? "",
      samples: num(item.samples) ?? 0,
      directionalCalls: num(item.directional_calls) ?? 0,
      hitRate: num(item.hit_rate),
      longMeanExcess: num(item.long_mean_excess),
      shortMeanExcess: num(item.short_mean_excess),
      longShortSpread: num(item.long_short_spread),
      meanSignedExcess: num(item.mean_signed_excess),
    })),
    byRating: listOf(data.by_rating).map((item) => ({
      rating: asString(item.rating) ?? "",
      count: num(item.count) ?? 0,
      meanExcess: num(item.mean_excess),
      baselineCount: num(item.baseline_count) ?? 0,
      baselineMeanExcess: num(item.baseline_mean_excess),
    })),
    rows: listOf(data.rows).map((item) => ({
      symbol: asString(item.symbol) ?? "",
      asOf: asString(item.as_of) ?? "",
      rating: asString(item.rating) ?? "",
      baseline: asString(item.baseline) ?? "",
      excess: num(item.excess_return),
    })),
  }
}

/** 比率 → 带符号百分比；缺数据写「—」。 */
export function signedPercent(value: number | null, digits = 2): string {
  if (value === null) {
    return "—"
  }
  const pct = value * 100
  return `${pct > 0 ? "+" : ""}${pct.toFixed(digits)}%`
}

export function ratingTone(rating: string): "rise" | "fall" | "flat" | "none" {
  if (rating === "Buy" || rating === "Overweight") {
    return "rise"
  }
  if (rating === "Sell" || rating === "Underweight") {
    return "fall"
  }
  return rating === "Hold" ? "flat" : "none"
}

type Refresh = () => Promise<string | null>

async function call(path: string, init: RequestInit, token: string | null, refresh: Refresh) {
  const response = await authorizedFetch(path, init, token, refresh)
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("x-request-id"))
  }
  return body
}

const json = (payload: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
})

export interface RunRequest {
  symbol: string
  as_of: string
  max_debate_rounds: number
  holding_days: number
  risk_strictness: "moderate" | "conservative"
  use_memory: boolean
  engine: "rules" | "tradingagents"
}

export async function createRun(input: RunRequest, token: string | null, refresh: Refresh) {
  return parseRun(await call("/api/v1/simulation/runs", json(input), token, refresh))
}

export async function fetchRun(id: string, token: string | null, refresh: Refresh) {
  return parseRun(await call(`/api/v1/simulation/runs/${id}`, { method: "GET" }, token, refresh))
}

export async function fetchRuns(token: string | null, refresh: Refresh) {
  return parseRunList(await call("/api/v1/simulation/runs", { method: "GET" }, token, refresh))
}

export async function settleRun(id: string, token: string | null, refresh: Refresh) {
  const body = await call(`/api/v1/simulation/runs/${id}/backtest`, { method: "POST" }, token, refresh)
  const data = dataOf(body)
  return data ? parseEvaluation(data) : null
}

export async function fetchReport(id: string, token: string | null, refresh: Refresh) {
  const body = await call(`/api/v1/simulation/runs/${id}/report`, { method: "GET" }, token, refresh)
  return asString(dataOf(body)?.markdown) ?? ""
}

export interface ExperimentRequest {
  symbols: string[]
  start: string
  end: string
  step: number
  holding_days: number
}

export async function runExperiment(input: ExperimentRequest, token: string | null, refresh: Refresh) {
  return parseExperiment(await call("/api/v1/simulation/experiments", json(input), token, refresh))
}
