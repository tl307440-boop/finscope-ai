import { describe, expect, it } from "vitest"
import { applyTheme, readTheme, shellLayout, toggleTheme } from "../../src/shell/theme"

describe("主布局", () => {
  it("1440 使用侧栏，390 改成单列", () => {
    expect(shellLayout(1440)).toBe("sidebar")
    expect(shellLayout(390)).toBe("stack")
    expect(shellLayout(767)).toBe("stack")
    expect(shellLayout(768)).toBe("sidebar")
  })

  it("默认深色，可以切到浅色", () => {
    expect(readTheme(null)).toBe("dark")
    expect(toggleTheme("dark")).toBe("light")
    expect(toggleTheme("light")).toBe("dark")
    const storage = {
      value: "",
      setItem(_key: string, value: string) {
        this.value = value
      },
    }
    applyTheme("light", storage as unknown as Storage)
    expect(document.documentElement.dataset.theme).toBe("light")
    expect(storage.value).toBe("light")
    applyTheme("dark", storage as unknown as Storage)
  })
})
