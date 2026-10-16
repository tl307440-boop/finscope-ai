<script setup lang="ts">
import { computed } from "vue"
import type { AnalystItem } from "../../../api/simulation"
import StatusPill from "../../../components/ui/StatusPill.vue"
import CitationChips from "./CitationChips.vue"

const props = defineProps<{ item: AnalystItem }>()

const STANCE: Record<string, { text: string; tone: "danger" | "ok" | "muted" | "info" }> = {
  bullish: { text: "偏多", tone: "danger" },
  bearish: { text: "偏空", tone: "ok" },
  neutral: { text: "中性", tone: "info" },
  unavailable: { text: "无数据", tone: "muted" },
}

const COVERAGE: Record<string, string> = { full: "数据完整", partial: "部分数据", unavailable: "未覆盖" }

const stance = computed(() => STANCE[props.item.stance] ?? STANCE.unavailable)
const offset = computed(() => (props.item.score === null ? 0 : props.item.score - 50))
</script>

<template>
  <article class="analyst" :class="`is-${item.stance}`">
    <header class="head">
      <h3>{{ item.role }}</h3>
      <StatusPill :tone="stance.tone" :text="stance.text" />
    </header>

    <div class="meter" :aria-label="item.score === null ? '不出分' : `评分 ${item.score}`">
      <span class="score tabular">{{ item.score === null ? "—" : item.score.toFixed(1) }}</span>
      <span class="track" aria-hidden="true">
        <span class="mid" />
        <span
          v-if="item.score !== null"
          class="fill"
          :class="offset >= 0 ? 'up' : 'down'"
          :style="{
            left: offset >= 0 ? '50%' : `${50 + offset}%`,
            width: `${Math.abs(offset)}%`,
          }"
        />
      </span>
      <span class="coverage">{{ COVERAGE[item.coverage] }}</span>
    </div>

    <p class="summary">{{ item.summary }}</p>

    <ul v-if="item.findings.length" class="findings">
      <li v-for="(finding, index) in item.findings" :key="index" :class="`p${finding.polarity + 1}`">
        <span class="mark" aria-hidden="true">{{ finding.polarity > 0 ? "▲" : finding.polarity < 0 ? "▼" : "■" }}</span>
        <span class="text">
          {{ finding.text }}
          <CitationChips :items="finding.citations" />
        </span>
      </li>
    </ul>
    <ul v-if="item.gaps.length" class="gaps">
      <li v-for="gap in item.gaps" :key="gap">{{ gap }}</li>
    </ul>
  </article>
</template>

<style scoped>
.analyst {
  display: grid;
  align-content: start;
  gap: 10px;
  min-width: 0;
  padding: 2px 16px;
  border-left: 1px solid var(--line-soft);
}

.analyst:first-child {
  padding-left: 0;
  border-left: 0;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

h3 {
  margin: 0;
  font-size: 14px;
  color: var(--text-strong);
}

.meter {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
}

.score {
  font-family: var(--font-num);
  font-size: 22px;
  font-weight: 600;
  color: var(--text-strong);
  min-width: 3.2ch;
}

.track {
  position: relative;
  height: 6px;
  border-radius: 3px;
  background: var(--panel-raise);
  overflow: hidden;
}

.mid {
  position: absolute;
  left: 50%;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: var(--muted);
}

.fill {
  position: absolute;
  top: 0;
  bottom: 0;
  transition:
    width 400ms ease,
    left 400ms ease;
}

.fill.up {
  background: var(--rise);
}

.fill.down {
  background: var(--fall);
}

.coverage {
  font-size: 12px;
  color: var(--muted);
}

.summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--muted);
}

.findings,
.gaps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.findings li {
  display: grid;
  grid-template-columns: 14px 1fr;
  gap: 6px;
}

.mark {
  font-size: 10px;
  line-height: 20px;
}

.p2 .mark {
  color: var(--rise);
}

.p0 .mark {
  color: var(--fall);
}

.p1 .mark {
  color: var(--faint);
}

.text {
  color: var(--text);
  min-width: 0;
}

.gaps li {
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  color: var(--warn);
  font-size: 13px;
}

@media (prefers-reduced-motion: reduce) {
  .fill {
    transition: none;
  }
}
</style>
