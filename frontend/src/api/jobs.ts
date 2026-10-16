export function jobStatusLabel(status: string): string {
  if (status === "queued") {
    return "排队"
  }
  if (status === "running") {
    return "执行中"
  }
  if (status === "succeeded") {
    return "完成"
  }
  if (status === "failed") {
    return "失败"
  }
  if (status === "cancelled") {
    return "已取消"
  }
  return status
}

export function healthLabel(status: string | null): string {
  if (status === "up") {
    return "最近检查正常"
  }
  if (status === "down") {
    return "最近检查失败"
  }
  return "尚未检查"
}

export function enqueueMessage(created: boolean): string {
  return created ? "已加入队列" : "同一任务已在队列中，没有另开一条"
}

export type AccessMode = "public_csv" | "configured" | "not_required" | "unavailable"

export interface ProviderAccess {
  mode: AccessMode
  text: string
  tone: "ok" | "danger"
}

/**
 * 数据源接入方式。FRED 没有密钥时走官方公开 CSV（只有最新修订值），
 * 仍然算可用；旧版后端对这种情况回 not_required，这里一并归到公开 CSV。
 */
export function providerAccess(input: {
  code: string
  availability: string
  credentialPresent: boolean
}): ProviderAccess {
  if (input.availability === "public_csv") {
    return { mode: "public_csv", text: "公开 CSV", tone: "ok" }
  }
  if (input.availability === "unavailable") {
    return { mode: "unavailable", text: "未配置", tone: "danger" }
  }
  if (input.availability === "configured") {
    return { mode: "configured", text: "已配置密钥", tone: "ok" }
  }
  if (input.code === "fred" && !input.credentialPresent) {
    return { mode: "public_csv", text: "公开 CSV", tone: "ok" }
  }
  if (input.availability === "not_required") {
    return { mode: "not_required", text: "免密钥", tone: "ok" }
  }
  return { mode: "unavailable", text: "未配置", tone: "danger" }
}

const ADJUSTMENT_TEXT: Record<string, string> = {
  qfq: "前复权",
  hfq: "后复权",
}

/** 把去重键还原成「标的 · 区间」，认不出的格式返回空串，由页面决定是否显示。 */
export function describeJobScope(jobType: string, dedupeKey: string): string {
  const parts = dedupeKey.split(":")
  if (jobType === "akshare_daily" && parts.length === 5 && parts[0] === "akshare") {
    return joinScope(parts[1], parts[3], parts[4], ADJUSTMENT_TEXT[parts[2]] ?? "")
  }
  if (jobType === "fred_series" && parts.length === 4 && parts[0] === "fred") {
    return joinScope(parts[1], parts[2], parts[3], "")
  }
  return ""
}

function joinScope(subject: string, start: string, end: string, extra: string): string {
  const range = start === end ? start : `${start} ~ ${end}`
  return [subject, range, extra].filter(Boolean).join(" · ")
}
