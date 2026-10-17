<script setup lang="ts">
/**
 * 指标带单格（参考图顶部那一排）：名称 + 口径、迷你走势、大数值、▲/▼ 较上期、最新观测日。
 * 颜色只表示最新一期的方向（上升红、下降绿），不代表好坏。
 */
import MiniSpark from "../../../components/ui/MiniSpark.vue"
import type { TileDirection, TileView } from "../../../api/macroTiles"

defineProps<{ tile: TileView }>()

const SPARK_TONE: Record<TileDirection, "up" | "down" | "muted"> = {
  up: "up",
  down: "down",
  flat: "muted",
  none: "muted",
}
const ARROW: Record<TileDirection, string> = { up: "▲", down: "▼", flat: "", none: "" }
</script>

<template>
  <article class="ticker-tile" :class="`is-${tile.direction}`" role="listitem">
    <header>
      <div class="titles">
        <span class="label">
          {{ tile.label }}
          <span v-if="tile.info" class="info" :title="tile.info" aria-hidden="true">?</span>
        </span>
        <span class="caption">{{ tile.caption }}</span>
      </div>
      <MiniSpark
        v-if="tile.spark.length > 1"
        class="spark"
        :points="tile.spark"
        kind="area"
        :tone="SPARK_TONE[tile.direction]"
        :width="64"
        :height="28"
      />
    </header>
    <p class="value">
      {{ tile.value }}<small v-if="tile.unit && tile.value !== '—'">{{ tile.unit }}</small>
    </p>
    <footer>
      <span class="delta">
        <span v-if="ARROW[tile.direction]" class="arrow">{{ ARROW[tile.direction] }}</span>
        {{ tile.deltaText }}
      </span>
      <span class="date">{{ tile.date }}</span>
    </footer>
  </article>
</template>

<style scoped>
.ticker-tile {
  --tone: var(--muted);
  flex: 0 0 172px;
  scroll-snap-align: start;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 112px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: linear-gradient(180deg, rgb(96 140 220 / 7%), rgb(96 140 220 / 2%));
}

.ticker-tile.is-up {
  --tone: var(--rise);
}

.ticker-tile.is-down {
  --tone: var(--fall);
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
}

.titles {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  color: var(--text-strong);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.info {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border: 1px solid var(--line);
  border-radius: 50%;
  color: var(--muted);
  font-size: 9px;
  font-weight: 400;
  cursor: help;
}

.caption {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.spark {
  flex: none;
}

.value {
  margin: 0;
  overflow: hidden;
  color: var(--tone);
  font-family: var(--font-num);
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ticker-tile.is-flat .value,
.ticker-tile.is-none .value {
  color: var(--text-strong);
}

.value small {
  margin-left: 2px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: auto;
  font-size: 12px;
}

.delta {
  color: var(--tone);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.arrow {
  font-size: 10px;
}

.date {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}
</style>
