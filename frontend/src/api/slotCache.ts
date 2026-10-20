/**
 * 总览驾驶舱的客户端加载策略（stale-while-revalidate）：
 * - 记住每个模块上次成功的数据，切到别的页面再回来时先用它秒开，后台再刷新；
 * - 瞬时失败（断网、超时、429、5xx）最多重试一次，真正的 4xx 不重试；
 * - 重试有独立的、更短的时限，并在重试前随机等一小会儿（抖动），避免几个模块同时压向刚恢复的后端；
 * - 旧数据超过 `MAX_AGE_MS` 就不再展示，避免把隔夜的行情当成今天的。
 */
import { FetchFailure } from "./industryMarket"

const MAX_AGE_MS = 10 * 60 * 1000

/**
 * 每次尝试各自的时限（毫秒）。
 * 首次留足后端冷启动聚合上游的时间（实测冷开约 11 秒，最坏的单个上游含重试 25 秒）；
 * 重试通常落在后端已经算好（或已记下失败）的结果上，15 秒足够，不必再等 30 秒。
 * 最坏总耗时 = 30 + 抖动(0.8~1.6) + 15 ≈ 47 秒，而不是各自 40 秒再叠加等待的 81 秒。
 */
export const ATTEMPT_TIMEOUTS_MS = [30_000, 15_000] as const

const RETRY_DELAY_MIN_MS = 800
const RETRY_DELAY_JITTER_MS = 800

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

/** loader 收到本次尝试的时限，应原样交给 fetch（每次都要新建 AbortSignal，不能复用）。 */
export async function withRetry<T>(
  loader: (timeoutMs: number) => Promise<T>,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  random: () => number = Math.random,
): Promise<T> {
  try {
    return await loader(ATTEMPT_TIMEOUTS_MS[0])
  } catch (error) {
    if (!isTransient(error)) {
      throw error
    }
    await wait(RETRY_DELAY_MIN_MS + random() * RETRY_DELAY_JITTER_MS)
    return loader(ATTEMPT_TIMEOUTS_MS[1])
  }
}
