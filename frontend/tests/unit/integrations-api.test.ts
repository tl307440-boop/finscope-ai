import { afterEach, describe, expect, it, vi } from "vitest"
import {
  groupItems,
  parseIntegrationList,
  saveIntegration,
  sourceText,
  testIntegration,
} from "../../src/api/integrations"

const BODY = {
  data: {
    notice: "密钥加密保存",
    items: [
      {
        key: "TIINGO_API_KEY",
        label: "Tiingo API 密钥",
        group: "美股行情",
        secret: true,
        kind: "text",
        help: "官方数据源",
        help_url: "https://www.tiingo.com",
        placeholder: "",
        configured: true,
        source: "database",
        preview: "••••7890",
        updated_at: "2026-10-16T10:00:00+00:00",
        updated_by: "admin",
        problem: "",
      },
      {
        key: "ALLOW_REGISTRATION",
        label: "开放注册",
        group: "账号",
        secret: false,
        kind: "bool",
        configured: true,
        source: "environment",
        preview: "true",
      },
      { label: "没有 key 的脏数据" },
    ],
  },
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("integrations api", () => {
  it("解析列表并丢弃没有 key 的条目", () => {
    const list = parseIntegrationList(BODY)
    expect(list.items.map((item) => item.key)).toEqual(["TIINGO_API_KEY", "ALLOW_REGISTRATION"])
    expect(list.items[0]?.preview).toBe("••••7890")
    expect(list.items[1]?.kind).toBe("bool")
    expect(list.notice).toBe("密钥加密保存")
  })

  it("按分组聚合并保持顺序", () => {
    const groups = groupItems(parseIntegrationList(BODY).items)
    expect(groups.map((group) => group.group)).toEqual(["美股行情", "账号"])
  })

  it("来源文案区分已配置、环境变量与未配置", () => {
    const [tiingo, registration] = parseIntegrationList(BODY).items
    expect(sourceText(tiingo!)).toBe("已在此处配置")
    expect(sourceText(registration!)).toBe("默认值")
    expect(sourceText({ ...tiingo!, source: "none" })).toBe("未配置")
    expect(sourceText({ ...tiingo!, source: "environment" })).toBe("来自服务器环境变量")
  })

  it("保存时只把值放进请求体，并带上登录令牌", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ data: BODY.data.items[0] }), { status: 200 }))
    vi.stubGlobal("fetch", fetcher)
    const saved = await saveIntegration("TIINGO_API_KEY", "abc", "tok", async () => null)
    expect(saved?.key).toBe("TIINGO_API_KEY")
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain("/api/v1/admin/integrations/TIINGO_API_KEY")
    expect(init.method).toBe("PUT")
    expect(init.body).toBe(JSON.stringify({ value: "abc" }))
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok")
  })

  it("测试连接返回后端给的结论，失败时抛出后端的错误信息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ data: { ok: false, message: "被拒绝" } }), { status: 200 })),
    )
    expect(await testIntegration("FRED_API_KEY", null, "tok", async () => null)).toEqual({
      ok: false,
      message: "被拒绝",
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: { code: "FORBIDDEN", message: "无权限" } }), { status: 403 }),
      ),
    )
    await expect(testIntegration("FRED_API_KEY", null, "tok", async () => null)).rejects.toThrow("无权限")
  })
})
