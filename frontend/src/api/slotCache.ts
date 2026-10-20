/**
 * 总览驾驶舱的客户端加载策略（stale-while-revalidate）：
 * - 记住每个模块上次成功的数据，切到别的页面再回来时先用它秒开，后台再刷新；
 * - 瞬时失败（断网、502/503/504）自动重试一次，真正的 4xx 不重试；
 * - 旧数据超过 `MAX_AGE_MS` 就不再展示，避免把隔夜的行情当成今天的。
 */
import { FetchFailure } from "./industryMarket"

const MAX_AGE_MS = 10 * 60 * 1000
const RETRY_DELAY_MS = 1200

interface Remembered {
  value: unknown
  at: number
}

const memory = new Map<string, Remembered>()

export function remember(key: string, value: unknown, now = Date.now()): void {
  memory.set(key, { value, at: now })
}

export function recall<T>(key: string, now = Date.now()): T | null {
  const hit = memory.get(key)
  if (!hit) {
    return null
  }
  if (now - hit.at > MAX_AGE_MS) {
    memory.delete(key)
    return null
  }
  return hit.value as T
}

export function forgetAll(): void {
  memory.clear()
}

/** 网络错误（status 0）和网关 / 上游 5xx 值得再试一次；其它错误（鉴权、参数）重试没有意义。 */
export function isTransient(error: unknown): boolean {
  if (error instanceof FetchFailure) {
    return error.status === 0 || error.status === 429 || error.status >= 500
  }
  return error instanceof TypeError
}

export async function withRetry<T>(
  loader: () => Promise<T>,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
): Promise<T> {
  try {
    return await loader()
  } catch (error) {
    if (!isTransient(error)) {
      throw error
    }
    await wait(RETRY_DELAY_MS)
    return loader()
  }
}
