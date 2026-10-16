<script setup lang="ts">
/**
 * 推演流水线拓扑：分析师（并行）→ 多空辩论 → 交易员 → 风控三方 → 组合经理。
 * 节点状态只来自后端返回的结果；运行中只显示不确定进度的扫描动画，不假装知道走到了哪一步。
 */
import { computed } from "vue"
import type { SimulationRun } from "../../../api/simulation"

const props = defineProps<{ run: SimulationRun | null; running: boolean }>()

type NodeState = "idle" | "done" | "off" | "veto" | "bull" | "bear"

interface Node {
  key: string
  label: string
  sub: string
  state: NodeState
}

const STANCE_STATE: Record<string, NodeState> = {
  bullish: "bull",
  bearish: "bear",
  neutral: "done",
  unavailable: "off",
}

const SHORT: Record<string, string> = {
  fundamental: "基本面",
  technical: "技术面",
  macro_news: "宏观资讯",
  sentiment: "市场情绪",
}

const columns = computed<{ title: string; nodes: Node[] }[]>(() => {
  const run = props.run
  const analysts = ["fundamental", "technical", "macro_news", "sentiment"].map((key) => {
    const found = run?.analysts.find((item) => item.analyst === key)
    return {
      key,
      label: SHORT[key],
      sub: !found ? "待命" : found.score === null ? "无数据" : `${found.score.toFixed(1)} 分`,
      state: found ? (STANCE_STATE[found.stance] ?? "done") : "idle",
    } satisfies Node
  })
  const rounds = run?.debates.length ?? 0
  const converged = run?.debates.some((item) => item.converged) ?? false
  const vetoes = run?.risks.filter((item) => item.vetoReason).length ?? 0
  const final = run?.proposals.at(-1)
  const riskNode = (profile: "aggressive" | "neutral" | "conservative", label: string): Node => {
    const last = run?.risks.filter((item) => item.profile === profile).at(-1)
    const vetoed = run?.risks.some((item) => item.profile === profile && item.vetoReason) ?? false
    return {
      key: profile,
      label,
      sub: !last ? "待命" : vetoed ? "曾否决" : last.approved ? "通过" : "保留",
      state: !last ? "idle" : vetoed ? "veto" : "done",
    }
  }
  return [
    { title: "分析师团队", nodes: analysts },
    {
      title: "多空辩论",
      nodes: [
        { key: "bull", label: "多头研究员", sub: run ? `${rounds} 轮` : "待命", state: run ? "bull" : "idle" },
        { key: "bear", label: "空头研究员", sub: run ? `${rounds} 轮` : "待命", state: run ? "bear" : "idle" },
        {
          key: "lead",
          label: "主导研究员",
          sub: !run ? "待命" : converged ? "已收敛" : "未收敛",
          state: run ? "done" : "idle",
        },
      ],
    },
    {
      title: "交易员",
      nodes: [
        {
          key: "trader",
          label: "模拟提案",
          sub: !run ? "待命" : `${run.proposals.length} 版${vetoes ? ` · 修正 ${vetoes}` : ""}`,
          state: run ? "done" : "idle",
        },
      ],
    },
    {
      title: "风控委员会",
      nodes: [riskNode("aggressive", "激进"), riskNode("neutral", "中性"), riskNode("conservative", "保守")],
    },
    {
      title: "组合经理",
      nodes: [
        {
          key: "pm",
          label: "签署决议",
          sub: !run ? "待命" : run.rating === "REVIEW" ? "无评级" : (final?.displayRating ?? run.rating),
          state: !run ? "idle" : run.rating === "REVIEW" ? "off" : "done",
        },
      ],
    },
  ]
})
</script>

<template>
  <div class="pipeline" :class="{ running }" role="list" aria-label="推演流水线">
    <section v-for="(column, index) in columns" :key="column.title" class="stage" role="listitem">
      <header class="stage-head">
        <span class="stage-no tabular">{{ index + 1 }}</span>
        <span>{{ column.title }}</span>
      </header>
      <ul class="nodes">
        <li
          v-for="node in column.nodes"
          :key="node.key"
          class="node"
          :class="`is-${node.state}`"
          :style="{ '--delay': `${index * 180}ms` }"
        >
          <span class="node-dot" aria-hidden="true" />
          <span class="node-label">{{ node.label }}</span>
          <span class="node-sub tabular">{{ node.sub }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.pipeline {
  display: grid;
  grid-template-columns: 1.15fr 1fr 0.9fr 1fr 0.9fr;
  gap: 18px;
  position: relative;
}

.stage {
  position: relative;
  display: grid;
  align-content: start;
  gap: 8px;
  min-width: 0;
}

.stage:not(:last-child)::after {
  content: "";
  position: absolute;
  top: 50%;
  right: -16px;
  width: 14px;
  height: 1px;
  background: linear-gradient(90deg, var(--edge-light), transparent);
}

.stage-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.stage-no {
  display: inline-grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid var(--line);
  font-size: 11px;
  color: var(--text);
}

.nodes {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
}

.node {
  display: grid;
  grid-template-columns: 8px auto 1fr;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line-soft);
  background: var(--panel-deep);
  font-size: 13px;
  transition:
    border-color 200ms ease,
    background-color 200ms ease;
}

.node-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--faint);
}

.node-label {
  color: var(--text);
  white-space: nowrap;
}

.node-sub {
  font-size: 12px;
  color: var(--muted);
  text-align: right;
  white-space: nowrap;
}

.is-done {
  border-color: var(--line);
}

.is-done .node-dot {
  background: var(--accent);
}

.is-bull {
  border-color: color-mix(in srgb, var(--rise) 35%, transparent);
}

.is-bull .node-dot {
  background: var(--rise);
}

.is-bear {
  border-color: color-mix(in srgb, var(--fall) 35%, transparent);
}

.is-bear .node-dot {
  background: var(--fall);
}

.is-veto {
  border-color: color-mix(in srgb, var(--warn) 45%, transparent);
}

.is-veto .node-dot {
  background: var(--warn);
}

.is-off {
  border-style: dashed;
  border-color: var(--line);
  background: transparent;
}

.is-off .node-label {
  color: var(--muted);
}

.running .node {
  animation: scan 1.6s ease-in-out infinite;
  animation-delay: var(--delay);
}

@keyframes scan {
  0%,
  100% {
    border-color: var(--line-soft);
  }

  45% {
    border-color: var(--edge-light);
    background: var(--panel-raise);
  }
}

@media (prefers-reduced-motion: reduce) {
  .running .node {
    animation: none;
  }
}

@media (max-width: 1180px) {
  .pipeline {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .stage::after {
    display: none;
  }
}
</style>
