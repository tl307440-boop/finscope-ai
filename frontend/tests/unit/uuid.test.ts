import { describe, expect, it } from "vitest"
import { newUuid } from "../../src/api/uuid"

const V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

describe("newUuid", () => {
  it("安全上下文里用原生 randomUUID", () => {
    const native = {
      randomUUID: (): `${string}-${string}-${string}-${string}-${string}` =>
        "11111111-2222-4333-8444-555555555555",
      getRandomValues: <T extends ArrayBufferView | null>(array: T): T => array,
    }
    const id = newUuid(native)
    expect(id).toBe("11111111-2222-4333-8444-555555555555")
  })

  it("HTTP 访问没有 randomUUID 时也能生成合法的 v4，且不重复", () => {
    const fallback = {
      getRandomValues: <T extends ArrayBufferView | null>(array: T): T => {
        const bytes = array as unknown as Uint8Array
        for (let index = 0; index < bytes.length; index += 1) {
          bytes[index] = Math.floor(Math.random() * 256)
        }
        return array
      },
    }
    const first = newUuid(fallback)
    const second = newUuid(fallback)
    expect(first).toMatch(V4)
    expect(second).toMatch(V4)
    expect(first).not.toBe(second)
  })

  it("固定随机字节时结果可复现，版本位与变体位正确", () => {
    const zeros = { getRandomValues: <T extends ArrayBufferView | null>(array: T): T => array }
    expect(newUuid(zeros)).toBe("00000000-0000-4000-8000-000000000000")
  })
})
