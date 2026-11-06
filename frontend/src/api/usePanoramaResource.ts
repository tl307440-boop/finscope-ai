/**
 * 看板面板的统一取数 composable：loading / ready / error 三态 + 定时刷新。
 * 数据是否过期、是否收盘由各面板根据 `meta` 自行展示（见 PanoramaStatusBar）。
 */

import { onBeforeUnmount, onMounted, ref, shallowRef, type Ref, type ShallowRef } from "vue"

import { errorMessage } from "./aSharePanorama"

export type ResourceState = "loading" | "ready" | "error"

export interface PanoramaResource<T> {
  data: ShallowRef<T | null>
  state: Ref<ResourceState>
  error: Ref<string>
  reload: () => Promise<void>
}

export function usePanoramaResource<T>(
  fetcher: () => Promise<T>,
  options: { pollMs?: number } = {},
): PanoramaResource<T> {
  const data = shallowRef<T | null>(null)
  const state = ref<ResourceState>("loading")
  const error = ref("")
  let timer: number | null = null
  let seq = 0

  async function reload(): Promise<void> {
    const current = ++seq
    // 已有数据时静默刷新，不闪回骨架
    if (data.value === null) state.value = "loading"
    try {
      const result = await fetcher()
      if (current !== seq) return
      data.value = result
      error.value = ""
      state.value = "ready"
    } catch (e) {
      if (current !== seq) return
      error.value = errorMessage(e)
      // 有旧数据就保留展示，只在没有数据时进入错误态
      state.value = data.value === null ? "error" : "ready"
    }
  }

  onMounted(() => {
    void reload()
    if (options.pollMs) {
      timer = window.setInterval(() => void reload(), options.pollMs)
    }
  })

  onBeforeUnmount(() => {
    seq += 1
    if (timer !== null) window.clearInterval(timer)
  })

  return { data, state, error, reload }
}
