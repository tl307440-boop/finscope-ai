import { describe, expect, it } from "vitest"
import { emptyReplyText, failureText, parseSseLine, parseTurn } from "../../src/api/assistantStream"

/** 复刻视图里 readStream 的循环：逐行解析，累加 delta，遇到 done 结束。 */
function readAll(raw: string): { text: string; done: boolean } {
  let text = ""
  for (const line of raw.split("\n")) {
    const payload = parseSseLine(line)
    if (!payload) {
      continue
    }
    if (typeof payload.delta === "string") {
      text += payload.delta
    }
    if (payload.done === true) {
      return { text, done: true }
    }
  }
  return { text, done: false }
}

function errorResponse(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: { code: "X", message }, request_id: "r1" }), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("助手 SSE 解析", () => {
  it("新协议的 delta / done 照常读出，type 与 status 被忽略", () => {
    const raw =
      'data: {"type":"delta","delta":"结论先行。"}\n\n' +
      'data: {"type":"delta","delta":"再看数据。"}\n\n' +
      'data: {"type":"done","done":true,"status":"completed"}\n\n'
    expect(readAll(raw)).toEqual({ text: "结论先行。再看数据。", done: true })
  })

  it("心跳注释行与空行不是事件，夹在中间也不影响正文", () => {
    const raw =
      ": ping\n\n" +
      'data: {"type":"delta","delta":"慢"}\n\n' +
      ": ping\n\n: ping\n\n" +
      'data: {"type":"delta","delta":"回答"}\n\n' +
      'data: {"type":"done","done":true,"status":"completed"}\n\n'
    expect(readAll(raw)).toEqual({ text: "慢回答", done: true })
    expect(parseSseLine(": ping")).toBeNull()
    expect(parseSseLine("")).toBeNull()
  })

  it("失败回合只有 error 与 done：没有正文，但能拿到结束信号，不会一直等", () => {
    const raw =
      'data: {"type":"error","code":"TURN_FAILED","message":"没有成功"}\n\n' +
      'data: {"type":"done","done":true,"status":"failed"}\n\n'
    expect(readAll(raw)).toEqual({ text: "", done: true })
  })

  it("损坏的 data 行被丢弃而不是抛错", () => {
    expect(parseSseLine("data: {不是 json")).toBeNull()
    expect(parseSseLine("data: [1, 2]")).toBeNull()
  })
})

describe("发送失败的提示", () => {
  it("429 与 409 显示后端写给用户的原因", async () => {
    expect(await failureText(errorResponse(429, "一分钟内最多提问 6 次，请 20 秒后再试。"))).toBe(
      "一分钟内最多提问 6 次，请 20 秒后再试。",
    )
    expect(await failureText(errorResponse(409, "这条提问的回答还在生成，请稍后刷新查看"))).toBe(
      "这条提问的回答还在生成，请稍后刷新查看",
    )
  })

  it("其他状态码只给状态码，不展示服务器给的文字", async () => {
    const text = await failureText(errorResponse(500, "内部细节 stack trace"))
    expect(text).toBe("发送失败（状态码 500），请稍后重试")
  })

  it("429 的响应体不是 JSON 时退回通用提示", async () => {
    const response = new Response("<html>Too Many</html>", { status: 429 })
    expect(await failureText(response)).toBe("发送失败（状态码 429），请稍后重试")
  })
})

describe("没有正文时的提示", () => {
  it("生成中、失败、已取消各说各的", () => {
    expect(emptyReplyText("streaming")).toBe("正在生成…")
    expect(emptyReplyText("failed")).toContain("没有生成成功")
    expect(emptyReplyText("cancelled")).toBe("这条回答已取消。")
    expect(emptyReplyText("completed")).toBe("（没有内容）")
  })
})

describe("读一条历史消息", () => {
  const base = { id: "m1", client_message_id: "c1", role: "assistant", completion_status: "completed" }

  it("正文为空的回答也要保留：只剩数据块的回答不能被丢掉", () => {
    const turn = parseTurn({
      ...base,
      content: "",
      blocks: [
        { type: "notice", code: "NOT_GROUNDED", level: "warning", message: "没能生成可核对的文字回答" },
      ],
      grounding: "none",
      as_of: "2026-08-01",
    })
    expect(turn).not.toBeNull()
    expect(turn?.content).toBe("")
    expect(turn?.completionStatus).toBe("completed")
    expect(turn?.extras.blocks).toHaveLength(1)
    expect(turn?.extras.asOf).toBe("2026-08-01")
  })

  it("失败、取消、仍在生成的空回答照样显示，由界面按状态说明", () => {
    for (const status of ["failed", "cancelled", "streaming"]) {
      const turn = parseTurn({ ...base, content: "", completion_status: status })
      expect(turn?.completionStatus).toBe(status)
    }
  })

  it("旧消息没有任何新字段，照常读出", () => {
    const turn = parseTurn({ ...base, content: "旧回答。" })
    expect(turn?.content).toBe("旧回答。")
    expect(turn?.extras.blocks).toEqual([])
    expect(turn?.extras.steps).toEqual([])
  })

  it("用户消息不带步骤和块", () => {
    const turn = parseTurn({ ...base, role: "user", content: "问题", blocks: [{ type: "notice" }] })
    expect(turn?.extras.blocks).toEqual([])
    expect(turn?.hasChart).toBe(false)
  })

  it("带走势图的用户消息记下标记，图片另取", () => {
    const turn = parseTurn({ ...base, role: "user", content: "看图", has_chart: true })
    expect(turn?.hasChart).toBe(true)
  })

  it("缺 id、缺角色、正文不是字符串才丢弃", () => {
    expect(parseTurn({ ...base, id: "", content: "x" })).toBeNull()
    expect(parseTurn({ ...base, role: undefined, content: "x" })).toBeNull()
    expect(parseTurn({ ...base, content: null })).toBeNull()
    expect(parseTurn({ ...base })).toBeNull()
  })
})
