/**
 * AI 助手的流式协议与失败提示（规格 §4）。纯函数，便于离线测试。
 *
 * 协议只用 `data:` 行，每行一个 JSON；`delta` 是正文片段，`done` 表示结束。
 * 后端新增的 `type`、`status` 字段这里不用，旧逻辑照常工作；
 * 空闲时后端补的 `: ping` 是 SSE 注释行，按规范不是事件，直接忽略。
 */
import { readJson } from "./client"
import { asRecord, asString } from "./json"

/** 解析一行 SSE。不是 `data:` 行（空行、心跳注释）或 JSON 不合法时返回 null。 */
export function parseSseLine(line: string): Record<string, unknown> | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith("data:")) {
    return null
  }
  try {
    return asRecord(JSON.parse(trimmed.slice(5).trim()) as unknown)
  } catch {
    return null
  }
}

/**
 * 发送被拒的提示。429（限流）与 409（同一条还在生成）的文字是后端专门写给用户看的，
 * 带着该等多久、哪种上限，直接显示；其他状态码只给状态码，不展示服务器内部信息。
 */
export async function failureText(response: Response): Promise<string> {
  if (response.status === 429 || response.status === 409) {
    try {
      const error = asRecord(asRecord(await readJson(response))?.error)
      const text = asString(error?.message)
      if (text) {
        return text
      }
    } catch {
      // 解析不了就走下面的通用提示
    }
  }
  return `发送失败（状态码 ${response.status}），请稍后重试`
}

/** 助手那一条没有正文时显示什么：生成中、失败和已取消要分开说，不能一直转圈。 */
export function emptyReplyText(status: string): string {
  if (status === "failed") {
    return "这条回答没有生成成功，可以点「重新生成」再试一次。"
  }
  if (status === "cancelled") {
    return "这条回答已取消。"
  }
  if (status === "completed") {
    return "（没有内容）"
  }
  return "正在生成…"
}
