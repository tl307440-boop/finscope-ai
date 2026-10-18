import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue"
import ElementPlus from "element-plus"
import { afterEach, describe, expect, it, vi } from "vitest"
import { parseBinding, parseQrState, parseSync, startQr } from "../../src/api/eastmoneyBinding"
import { clearSession, useSessionStore } from "../../src/stores/auth"
import EastmoneyBindPanel from "../../src/views/parts/watchlist/EastmoneyBindPanel.vue"

afterEach(() => {
  cleanup()
  clearSession()
  vi.unstubAllGlobals()
})

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data, request_id: "r" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("东财绑定接口解析", () => {
  it("后端省略的字段按空值处理", () => {
    expect(parseBinding({ bound: false })).toEqual({
      bound: false,
      status: "",
      alias: "",
      boundAt: "",
      lastSyncAt: "",
      message: "",
      groups: null,
      items: null,
    })
  })

  it("已绑定状态保留同步摘要，未知状态当作空", () => {
    const parsed = parseBinding({ bound: true, status: "weird", alias: "老王", groups: 7, items: 423 })
    expect(parsed.bound).toBe(true)
    expect(parsed.status).toBe("")
    expect(parsed.alias).toBe("老王")
    expect(parsed.groups).toBe(7)
    expect(parsed.items).toBe(423)
  })

  it("同步结果只认字符串分组名", () => {
    const parsed = parseSync({ groups: 7, items: 423, added: 3, removed: 1, skipped_groups: ["a", 1, null], cached: true })
    expect(parsed).toMatchObject({ groups: 7, items: 423, added: 3, removed: 1, cached: true })
    expect(parsed?.skippedGroups).toEqual(["a"])
    expect(parseSync(null)).toBeNull()
    expect(parseSync([])).toBeNull()
  })

  it("扫码状态：未知值按等待处理，成功时带同步结果", () => {
    expect(parseQrState({ state: "???" }).phase).toBe("waiting")
    const done = parseQrState({ state: "success", alias: "x", sync: { groups: 1, items: 2, added: 2, removed: 0, message: "ok" } })
    expect(done.phase).toBe("success")
    expect(done.sync?.items).toBe(2)
    expect(parseQrState({ state: "expired" }).phase).toBe("expired")
  })

  it("二维码缺图时报错而不是渲染空图", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => envelope({ login_id: "abc" })))
    await expect(startQr("t", async () => null)).rejects.toThrow("二维码生成失败")
  })

  it("二维码转成 data URL", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => envelope({ login_id: "abc", qr_png_base64: "AAAA", expires_in: 150 })))
    const code = await startQr("t", async () => null)
    expect(code).toEqual({ loginId: "abc", imageSrc: "data:image/png;base64,AAAA", expiresIn: 150 })
  })
})

describe("东财绑定卡片", () => {
  function mountPanel(handler: (path: string, init: RequestInit) => Response) {
    const session = useSessionStore()
    session.accessToken = "token"
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) =>
      handler(String(input), init ?? {}),
    )
    vi.stubGlobal("fetch", fetchMock)
    const view = render(EastmoneyBindPanel, {
      global: { plugins: [ElementPlus], stubs: { "router-link": true } },
    })
    return { view, fetchMock }
  }

  it("未绑定时给出绑定入口，点开后显示二维码", async () => {
    const { fetchMock } = mountPanel((path, init) => {
      if (path.endsWith("/eastmoney/qr") && init.method === "POST") {
        return envelope({ login_id: "abc", qr_png_base64: "AAAA", expires_in: 150 })
      }
      return envelope({ bound: false })
    })
    const button = await screen.findByRole("button", { name: "绑定东方财富" })
    await fireEvent.click(button)
    const image = await screen.findByAltText("东方财富登录二维码")
    expect(image.getAttribute("src")).toBe("data:image/png;base64,AAAA")
    expect(fetchMock).toHaveBeenCalled()
  })

  it("登录失效时提示重新扫码，且不提供立即同步", async () => {
    mountPanel(() => envelope({ bound: true, status: "expired", alias: "老王" }))
    expect(await screen.findByText("登录已失效")).toBeTruthy()
    expect(screen.getByRole("button", { name: "重新扫码" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "立即同步" })).toBeNull()
  })

  it("已绑定时显示账号和同步摘要，立即同步后触发刷新事件", async () => {
    const { view } = mountPanel((path, init) => {
      if (path.endsWith("/eastmoney/sync") && init.method === "POST") {
        return envelope({ groups: 7, items: 423, added: 5, removed: 0, message: "同步完成" })
      }
      return envelope({ bound: true, status: "active", alias: "老王", groups: 7, items: 423 })
    })
    expect(await screen.findByText("老王")).toBeTruthy()
    expect(await screen.findByText(/7 个分组、423 个标的/)).toBeTruthy()
    await fireEvent.click(screen.getByRole("button", { name: "立即同步" }))
    await waitFor(() => expect(view.emitted().synced?.length ?? 0).toBeGreaterThan(0))
  })
})
