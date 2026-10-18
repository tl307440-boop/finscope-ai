<script setup lang="ts">
/**
 * 未来宏观情景（统计口径）：
 *  - 象限转移：历史上处于当前象限的季度，下一个季度分别落在哪个象限（次数 k / n）；
 *  - 区间：相邻季度变化量的 10% / 50% / 90% 分位数叠加到最新值上。
 * 不是预测，不是概率承诺。参考图里无出处的「50% / 30% / 20%」不会出现在这里。
 */
import { computed } from "vue"
import { buildScenario, QUADRANT_TEXT, MIN_CHANGES, MIN_TRANSITIONS, type QuadrantModel } from "../../../api/macroStats"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const props = defineProps<{
  model: QuadrantModel | null
  growthLabel: string
  inflationLabel: string
}>()

const scenario = computed(() => (props.model ? buildScenario(props.model) : null))

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(0)}%`
}

function fixed(value: number): string {
  return value.toFixed(1)
}
</script>

<template>
  <PanelCard
    title="未来宏观行情推演"
    info="历史统计口径：象限转移次数与季度变化量分位数，不是预测。样本不足时只给次数、不给百分比，也不给区间"
    tone="violet"
  >
    <template #icon><span class="panel-icon">◇</span></template>
    <StateBlock
      v-if="!scenario"
      state="empty"
      message="增长与通胀配对的季度不足 4 个，无法做历史统计推演"
    />
    <template v-else>
      <p class="lead">
        当前象限：<strong>{{ QUADRANT_TEXT[scenario.from].label }}</strong>（{{ QUADRANT_TEXT[scenario.from].sub }}）。
        历史上处于该象限且下一季度已有数据的季度共 <strong>{{ scenario.transitions }}</strong> 个。
      </p>

      <table class="rows">
        <caption class="sr-only">下一季度象限的历史分布</caption>
        <thead>
          <tr>
            <th>下一季度象限</th>
            <th class="num">历史次数</th>
            <th class="num">历史占比</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in scenario.rows" :key="row.code">
            <td>{{ QUADRANT_TEXT[row.code].label }}</td>
            <td class="num">{{ row.count }} / {{ scenario.transitions }}</td>
            <td class="num">{{ percent(row.share) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="!scenario.sufficient" class="warn">
        样本不足（少于 {{ MIN_TRANSITIONS }} 次）：只显示次数，不给百分比。
      </p>

      <h3 class="section">下一季度历史分位区间</h3>
      <table v-if="scenario.growthRange || scenario.inflationRange" class="rows">
        <thead>
          <tr>
            <th>指标</th>
            <th class="num">最新</th>
            <th class="num">10%</th>
            <th class="num">50%</th>
            <th class="num">90%</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="scenario.growthRange">
            <td>{{ growthLabel }}</td>
            <td class="num">{{ fixed(scenario.growthRange.latest) }}%</td>
            <td class="num">{{ fixed(scenario.growthRange.low) }}%</td>
            <td class="num">{{ fixed(scenario.growthRange.mid) }}%</td>
            <td class="num">{{ fixed(scenario.growthRange.high) }}%</td>
          </tr>
          <tr v-if="scenario.inflationRange">
            <td>{{ inflationLabel }}</td>
            <td class="num">{{ fixed(scenario.inflationRange.latest) }}%</td>
            <td class="num">{{ fixed(scenario.inflationRange.low) }}%</td>
            <td class="num">{{ fixed(scenario.inflationRange.mid) }}%</td>
            <td class="num">{{ fixed(scenario.inflationRange.high) }}%</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="warn">相邻季度变化量样本不足（少于 {{ MIN_CHANGES }} 个），不给区间。</p>

      <p class="note">
        样本：{{ scenario.firstPeriod }} 至 {{ scenario.lastPeriod }}，共 {{ scenario.quarters }} 个季度；象限分界为该区间内的中位数
        （{{ growthLabel }} {{ fixed(scenario.growthMid) }}%，{{ inflationLabel }} {{ fixed(scenario.inflationMid) }}%）。
        这是对历史的描述性统计，不是对未来的预测，不构成投资建议。
      </p>
    </template>
  </PanelCard>
</template>

<style scoped>
.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.lead {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.7;
}

.rows {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.rows th,
.rows td {
  padding: 5px 4px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  font-weight: 400;
}

.rows th {
  color: var(--muted);
}

.rows .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.section {
  margin: 14px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
}

.warn {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--warn);
}

.note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
</style>
