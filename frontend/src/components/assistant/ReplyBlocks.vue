<script setup lang="ts">
/**
 * 服务端生成的渲染块：折线图、指标卡、表格和提示。
 * 数字都是后端按已入库数据给出的原文，这里不换算、不补数。
 */
import { VChart } from "../../charts"
import {
  cellText,
  lineChartOption,
  type AssistantBlock,
  type BlockColumn,
  type BlockRow,
} from "../../api/assistantBlocks"
import MetricTile from "../ui/MetricTile.vue"

defineProps<{ blocks: AssistantBlock[] }>()

function header(column: BlockColumn): string {
  return column.unit ? `${column.label}（${column.unit}）` : column.label
}

function rowKey(row: BlockRow, index: number): string {
  return `${index}-${Object.values(row).join("|")}`
}
</script>

<template>
  <div v-if="blocks.length" class="blocks">
    <template v-for="(block, index) in blocks" :key="`${block.type}-${index}`">
      <section v-if="block.type === 'metric_cards'" class="cards" aria-label="关键数据">
        <MetricTile
          v-for="item in block.items"
          :key="item.label"
          :label="item.label"
          :value="item.value"
          :unit="item.unit"
          :period="item.asOf"
          :delta-label="item.change !== null ? item.changeLabel : ''"
        >
          <template v-if="item.change !== null" #delta>
            <span class="change">{{ item.change }}</span>
          </template>
        </MetricTile>
      </section>

      <section v-else-if="block.type === 'line_chart'" class="chart-block">
        <h4>{{ block.title }}</h4>
        <VChart
          v-if="lineChartOption(block)"
          class="chart"
          :option="lineChartOption(block) ?? {}"
          autoresize
        />
        <p v-else class="muted">数据点不够，暂不画图。</p>
      </section>

      <section v-else-if="block.type === 'table'" class="table-block">
        <h4>{{ block.title }}</h4>
        <div class="scroller" tabindex="0" :aria-label="block.title || '数据表'">
          <table>
            <thead>
              <tr>
                <th v-for="column in block.columns" :key="column.key" scope="col">
                  {{ header(column) }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, at) in block.rows" :key="rowKey(row, at)">
                <td v-for="column in block.columns" :key="column.key">
                  {{ cellText(row[column.key] ?? null) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted foot">
          共 {{ block.rows.length }} 行<template v-if="block.truncated > 0">，更早的数据没有取完</template>
        </p>
      </section>

      <p v-else-if="block.type === 'notice'" class="notice" :data-level="block.level" role="status">
        {{ block.message }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.blocks {
  display: grid;
  gap: 12px;
  margin-top: 10px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

h4 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.chart {
  height: 200px;
}

.scroller {
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

th,
td {
  padding: 5px 10px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--line-soft);
}

th {
  position: sticky;
  top: 0;
  background: var(--panel-soft);
  color: var(--muted);
  font-weight: 500;
}

tbody tr:last-child td {
  border-bottom: 0;
}

.foot {
  margin: 4px 0 0;
  font-size: 12px;
}

.muted {
  color: var(--muted);
}

.change {
  font-variant-numeric: tabular-nums;
}

.notice {
  margin: 0;
  padding: 8px 12px;
  font-size: 13px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  color: var(--text);
}

.notice[data-level="warning"] {
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
  background: color-mix(in srgb, var(--warn) 9%, transparent);
}
</style>
