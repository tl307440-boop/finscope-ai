<script setup lang="ts">
/**
 * 指标带滑动容器：放不下时横向滚动。
 * 滚轮：悬停时纵向滚轮转成横向；已到对应端点就不拦截，让页面照常上下滚动（判定见 tickerWheel）。
 * 箭头与方向键的平滑滚动由 CSS scroll-behavior 负责，减少动态效果时自动关闭。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import MacroTickerTile from "./MacroTickerTile.vue"
import { atLeftEdge, atRightEdge, wheelScrollPx } from "./tickerWheel"
import type { TileView } from "../../../api/macroTiles"

const props = withDefaults(defineProps<{ tiles: readonly TileView[]; loading?: boolean }>(), { loading: false })

/** 单格宽 172 + 间距 10 */
const STEP = 182

const track = ref<HTMLElement | null>(null)
const leftButton = ref<HTMLButtonElement | null>(null)
const rightButton = ref<HTMLButtonElement | null>(null)
const canLeft = ref(false)
const canRight = ref(false)

function refresh(): void {
  const element = track.value
  if (!element) {
    return
  }
  const nextLeft = !atLeftEdge(element)
  const nextRight = !atRightEdge(element)
  const focused = document.activeElement
  if ((!nextLeft && focused === leftButton.value) || (!nextRight && focused === rightButton.value)) {
    element.focus({ preventScroll: true })
  }
  canLeft.value = nextLeft
  canRight.value = nextRight
}

function page(direction: 1 | -1): void {
  const element = track.value
  if (!element) {
    return
  }
  element.scrollBy({ left: direction * Math.max(STEP, element.clientWidth - STEP) })
}

function onWheel(event: WheelEvent): void {
  const element = track.value
  if (!element) {
    return
  }
  const px = wheelScrollPx({
    deltaX: event.deltaX,
    deltaY: event.deltaY,
    deltaMode: event.deltaMode,
    ctrlKey: event.ctrlKey,
    scrollLeft: element.scrollLeft,
    scrollWidth: element.scrollWidth,
    clientWidth: element.clientWidth,
  })
  if (px === null) {
    return
  }
  event.preventDefault()
  // 滚轮要跟手：显式 instant，绕过容器上的 CSS 平滑滚动
  element.scrollBy({ left: px, behavior: "instant" })
}

function onKey(event: KeyboardEvent): void {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
    return
  }
  event.preventDefault()
  track.value?.scrollBy({ left: event.key === "ArrowRight" ? STEP : -STEP })
}

let observer: ResizeObserver | null = null

onMounted(() => {
  refresh()
  if (typeof ResizeObserver !== "undefined" && track.value) {
    observer = new ResizeObserver(refresh)
    observer.observe(track.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
})

watch(
  () => [props.tiles.length, props.loading],
  () => {
    void nextTick(refresh)
  },
)
</script>

<template>
  <section class="ticker">
    <button
      v-show="canLeft"
      ref="leftButton"
      type="button"
      class="arrow left"
      aria-label="向左滚动指标"
      @click="page(-1)"
    >
      ‹
    </button>
    <div
      ref="track"
      class="track"
      role="list"
      tabindex="0"
      aria-label="美国宏观指标"
      :aria-busy="loading"
      @wheel="onWheel"
      @scroll.passive="refresh"
      @keydown="onKey"
    >
      <template v-if="loading">
        <div v-for="index in 8" :key="index" class="skeleton" aria-hidden="true" />
      </template>
      <template v-else>
        <MacroTickerTile v-for="tile in tiles" :key="tile.key" :tile="tile" />
      </template>
    </div>
    <button
      v-show="canRight"
      ref="rightButton"
      type="button"
      class="arrow right"
      aria-label="向右滚动指标"
      @click="page(1)"
    >
      ›
    </button>
  </section>
</template>

<style scoped>
.ticker {
  position: relative;
}

.track {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  scroll-behavior: smooth;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
  border-radius: var(--radius-sm);
}

.track::-webkit-scrollbar {
  display: none;
}

.track:focus {
  outline: none;
}

.track:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.skeleton {
  flex: 0 0 172px;
  height: 112px;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, rgb(96 140 220 / 6%), rgb(96 140 220 / 14%), rgb(96 140 220 / 6%));
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

@keyframes shimmer {
  from {
    background-position: 100% 0;
  }

  to {
    background-position: -100% 0;
  }
}

.arrow {
  position: absolute;
  top: 50%;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 44px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  font-size: 20px;
  line-height: 1;
  transform: translateY(-50%);
  cursor: pointer;
  box-shadow: 0 4px 14px rgb(0 0 0 / 35%);
}

.arrow:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.arrow.left {
  left: -6px;
}

.arrow.right {
  right: -6px;
}

@media (prefers-reduced-motion: reduce) {
  .track {
    scroll-behavior: auto;
  }

  .skeleton {
    animation: none;
  }
}
</style>
