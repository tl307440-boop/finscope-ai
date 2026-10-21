/**
 * 问答会话列表的纯函数：解析接口返回、按最近活动分组、改名校验。
 *
 * 后端：GET /api/v1/assistant/sessions 返回 { sessions: [{ id, title, created_at, updated_at }] }，
 * 已按 updated_at 倒序；这里仍按时间再排一次，前端新建或刚提问的会话本地顶到最前也不乱。
 * 每个会话的模型上下文只取本会话的历史，切换会话就是换一段独立的对话。
 */
import { asDate, asString, dataOf, listOf } from "./json"

export const TITLE_MAX = 80

export interface SessionSummary {
  id: string
  title: string
  /** 最近一次提问的时间（ISO，UTC）；没有就用创建时间。 */
  updatedAt: string
}

export function parseSession(value: Record<string, unknown> | null): SessionSummary | null {
  const id = asString(value?.id)
  if (!id) {
    return null
  }
  return {
    id,
    title: asString(value?.title) ?? "新的问答",
    updatedAt: asString(value?.updated_at) ?? asString(value?.created_at) ?? "",
  }
}

export function parseSessions(body: unknown): SessionSummary[] {
  return sortSessions(
    listOf(dataOf(body)?.sessions).flatMap((row) => {
      const parsed = parseSession(row)
      return parsed ? [parsed] : []
    }),
  )
}

function stamp(item: SessionSummary): number {
  return asDate(item.updatedAt)?.getTime() ?? 0
}

export function sortSessions(items: SessionSummary[]): SessionSummary[] {
  return [...items].sort((a, b) => stamp(b) - stamp(a))
}

/** 新建或刚提问的会话：替换同 id 的旧条目并顶到最前。 */
export function upsertSession(items: SessionSummary[], item: SessionSummary): SessionSummary[] {
  return [item, ...items.filter((existing) => existing.id !== item.id)]
}

export function removeSession(items: SessionSummary[], id: string): SessionSummary[] {
  return items.filter((item) => item.id !== id)
}

/** 改名：压掉多余空白；空的或超过 80 字返回原因，合法返回 null。 */
export function titleProblem(raw: string): string | null {
  const cleaned = cleanTitle(raw)
  if (!cleaned) {
    return "标题不能为空"
  }
  if (cleaned.length > TITLE_MAX) {
    return `标题最多 ${TITLE_MAX} 个字`
  }
  return null
}

export function cleanTitle(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ")
}

export type SessionGroup = "今天" | "昨天" | "近 7 天" | "更早"

/** 按浏览器本地日历日分组（时间按 UTC 存，按本地时区显示）。 */
export function groupOf(updatedAt: string, now: Date = new Date()): SessionGroup {
  const at = asDate(updatedAt)
  if (!at) {
    return "更早"
  }
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = 24 * 60 * 60 * 1000
  const time = at.getTime()
  if (time >= startOfToday) {
    return "今天"
  }
  if (time >= startOfToday - day) {
    return "昨天"
  }
  if (time >= startOfToday - 6 * day) {
    return "近 7 天"
  }
  return "更早"
}

export function groupSessions(
  items: SessionSummary[],
  now: Date = new Date(),
): { group: SessionGroup; items: SessionSummary[] }[] {
  const groups: { group: SessionGroup; items: SessionSummary[] }[] = []
  for (const item of items) {
    const group = groupOf(item.updatedAt, now)
    const last = groups[groups.length - 1]
    if (last && last.group === group) {
      last.items.push(item)
    } else {
      groups.push({ group, items: [item] })
    }
  }
  return groups
}
