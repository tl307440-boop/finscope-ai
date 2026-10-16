import { authorizedFetch, errorFrom, readJson } from "./client"
import { asRecord, asString, listOf } from "./json"

export interface IntegrationItem {
  key: string
  label: string
  group: string
  secret: boolean
  kind: "text" | "url" | "bool"
  help: string
  helpUrl: string
  placeholder: string
  configured: boolean
  source: "database" | "environment" | "none"
  /** 后端返回的掩码（密钥只有末四位）；明文永远不会回到前端。 */
  preview: string
  updatedAt: string | null
  updatedBy: string | null
  problem: string
}

export interface IntegrationList {
  items: IntegrationItem[]
  notice: string
}

export interface ProbeOutcome {
  ok: boolean
  message: string
}

type Refresh = () => Promise<string | null>

function parseKind(value: unknown): IntegrationItem["kind"] {
  return value === "url" || value === "bool" ? value : "text"
}

function parseSource(value: unknown): IntegrationItem["source"] {
  return value === "database" || value === "environment" ? value : "none"
}

export function parseItem(record: Record<string, unknown>): IntegrationItem | null {
  const key = asString(record.key)
  if (!key) {
    return null
  }
  return {
    key,
    label: asString(record.label) ?? key,
    group: asString(record.group) ?? "其他",
    secret: record.secret === true,
    kind: parseKind(record.kind),
    help: asString(record.help) ?? "",
    helpUrl: asString(record.help_url) ?? "",
    placeholder: asString(record.placeholder) ?? "",
    configured: record.configured === true,
    source: parseSource(record.source),
    preview: asString(record.preview) ?? "",
    updatedAt: asString(record.updated_at),
    updatedBy: asString(record.updated_by),
    problem: asString(record.problem) ?? "",
  }
}

export function parseIntegrationList(body: unknown): IntegrationList {
  const data = asRecord(asRecord(body)?.data)
  const items = listOf(data?.items).flatMap((record) => {
    const item = parseItem(record)
    return item ? [item] : []
  })
  return { items, notice: asString(data?.notice) ?? "" }
}

/** 按分组聚合，保持后端给出的顺序。 */
export function groupItems(items: IntegrationItem[]): { group: string; items: IntegrationItem[] }[] {
  const groups = new Map<string, IntegrationItem[]>()
  for (const item of items) {
    groups.set(item.group, [...(groups.get(item.group) ?? []), item])
  }
  return [...groups.entries()].map(([group, list]) => ({ group, items: list }))
}

const BASE = "/api/v1/admin/integrations"

async function call(
  path: string,
  init: RequestInit,
  token: string | null,
  refresh: Refresh,
): Promise<unknown> {
  const response = await authorizedFetch(path, init, token, refresh)
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return body
}

const JSON_HEADERS = { "Content-Type": "application/json" }

export async function fetchIntegrations(
  token: string | null,
  refresh: Refresh,
): Promise<IntegrationList> {
  return parseIntegrationList(await call(BASE, { method: "GET" }, token, refresh))
}

export async function saveIntegration(
  key: string,
  value: string,
  token: string | null,
  refresh: Refresh,
): Promise<IntegrationItem | null> {
  const body = await call(
    `${BASE}/${encodeURIComponent(key)}`,
    { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ value }) },
    token,
    refresh,
  )
  return parseItem(asRecord(asRecord(body)?.data) ?? {})
}

export async function clearIntegration(
  key: string,
  token: string | null,
  refresh: Refresh,
): Promise<IntegrationItem | null> {
  const body = await call(
    `${BASE}/${encodeURIComponent(key)}`,
    { method: "DELETE" },
    token,
    refresh,
  )
  return parseItem(asRecord(asRecord(body)?.data) ?? {})
}

export async function testIntegration(
  key: string,
  value: string | null,
  token: string | null,
  refresh: Refresh,
): Promise<ProbeOutcome> {
  const body = await call(
    `${BASE}/${encodeURIComponent(key)}/test`,
    { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ value }) },
    token,
    refresh,
  )
  const data = asRecord(asRecord(body)?.data)
  return { ok: data?.ok === true, message: asString(data?.message) ?? "没有返回结果" }
}

export function sourceText(item: IntegrationItem): string {
  if (item.source === "database") {
    return "已在此处配置"
  }
  if (item.source === "environment") {
    return item.kind === "bool" ? "默认值" : "来自服务器环境变量"
  }
  return "未配置"
}
