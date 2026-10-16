export function uploadStatusText(status: string, message: string): string {
  if (status === "duplicate") {
    return "这份文件已经上传过"
  }
  if (status === "ocr_required") {
    return "扫描件需要文字版 PDF，当前标记为待识别"
  }
  if (status === "failed") {
    return "文件损坏，已记录失败，不会进入解析"
  }
  if (status === "queued") {
    return "已接收，等待解析"
  }
  return message || "无法上传"
}

export interface DocumentStatus {
  text: string
  tone: "ok" | "warn" | "danger" | "accent" | "muted"
}

/** 已上传文件的解析状态；不认识的取值不直接展示英文枚举。 */
export function documentStatus(parseStatus: string): DocumentStatus {
  if (parseStatus === "parsed") {
    return { text: "可检索", tone: "ok" }
  }
  if (parseStatus === "parsing") {
    return { text: "解析中", tone: "accent" }
  }
  if (parseStatus === "queued") {
    return { text: "等待解析", tone: "warn" }
  }
  if (parseStatus === "ocr_required") {
    return { text: "需要文字版", tone: "warn" }
  }
  if (parseStatus === "failed") {
    return { text: "解析失败", tone: "danger" }
  }
  return { text: "状态未知", tone: "muted" }
}
