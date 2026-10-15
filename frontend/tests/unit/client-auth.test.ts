import { describe, expect, it, vi } from "vitest"
import { authorizedFetch } from "../../src/api/client"

describe("带认证的请求", () => {
  it("刷新成功后只重试一次", async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers)
      if (String(input).endsWith("/me") && headers.get("Authorization") !== "Bearer next") {
        return new Response("{}", { status: 401 })
      }
      return new Response("{}", { status: 200 })
    })
    const response = await authorizedFetch("/api/v1/me", { method: "GET" }, "old", async () => "next", fetcher)
    expect(response.status).toBe(200)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it("刷新失败时不再重复请求", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 401 }))
    const response = await authorizedFetch("/api/v1/me", { method: "GET" }, "old", async () => null, fetcher)
    expect(response.status).toBe(401)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
