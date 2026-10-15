import { createPinia, setActivePinia } from "pinia"
import { beforeEach } from "vitest"

// Pinia store 是全局单例，用例之间会串会话状态；每个用例前换一套干净的。
beforeEach(() => {
  setActivePinia(createPinia())
})
