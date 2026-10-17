<script setup lang="ts">
/**
 * 指标带滑动容器：放不下时横向滚动。
 * 滚轮：悬停时纵向滚轮转成横向；已到对应端点就不拦截，让页面照常上下滚动。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import MacroTickerTile from "./MacroTickerTile.vue"
import type { TileView } from "../../../api/macroTiles"

const props = withDefaults(defineProps<{ tiles: readonly TileView[]; loading?: boolean }>(), { loading: false })

/** 单格宽 172 + 间距 10 */
const STEP = 182

const track = ref<HTMLElement | null>(null)
const canLeft = ref(false)
const canRight = ref(false)

function refresh(): void {
  const element = track.value
  if (!element) {
    return
  }
  canLeft.value = element.scrollLeft > 1
  canRight.value = element.scrollLeft + element.clientWidth < element.scrollWidth - 1
}

function page(direction: 1 | -1): void {
  const element = track.value
  if (!element) {
    return
  }
  element.scrollBy({ left: direction * Math.max(STEP, element.clientWidth - STEP), behavior: "smooth" })
}

function onWheel(event: WheelEvent): void {
  const element = track.value
  if (!element) {
    return
  }
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
  if (delta === 0) {
    return
  }
  const atStart = element.scrollLeft <= 0
  const atEnd = element.scrollLeft + element.clientWidth >= element.scrollWidth - 1
  if ((delta < 0 && atStart) || (delta > 0 && atEnd)) {
    return
  }
  event.preventDefault()
  element.scrollLeft += delta
}

function onKey(event: KeyboardEvent): void {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
    return
  }
  event.preventDefault()
  track.value?.scrollBy({ left: event.key === "ArrowRight" ? STEP : -STEP, behavior: "smooth" })
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
  <section class="ticker" aria-label="美国宏观指标">
    <button v-show="canLeft" type="button" class="arrow left" aria-label="向左滚动指标" @click="page(-1)">‹</button>
    <div ref="track" class="track" role="list" tabindex="0" @wheel="onWheel" @scroll.passive="refresh" @keydown="onKey">
      <template v-if="loading">
        <div v-for="index in 8" :key="index" class="skeleton" aria-hidden="true" />
      </template>
      <template v-else>
        <MacroTickerTile v-for="tile in tiles" :key="tile.key" :tile="tile" />
      </template>
    </div>
    <button v-show="canRight" type="button" class="arrow right" aria-label="向右滚动指标" @click="page(1)">›</button>
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
  scroll-snap-type: x proximity;
  scrollbar-width: none;
  outline: none;
}

.track::-webkit-scrollbar {
  display: none;
}

.track:focus-visible {
  box-shadow: 0 0 0 2px var(--accent);
  border-radius: var(--radius-sm);
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
  .skeleton {
    animation: none;
  }
}
</style>
