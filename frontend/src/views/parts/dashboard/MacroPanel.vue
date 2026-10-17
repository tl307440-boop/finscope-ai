<script setup lang="ts">
/** 全球宏观概览：FRED / 东财数据中心指标卡、经济热度与流动性刻度、外盘资产。 */
import { computed } from "vue"
import { Odometer } from "@element-plus/icons-vue"
import type { MacroOverview, MacroTile } from "../../../api/dashboard"
import DeltaValue from "../../../components/ui/DeltaValue.vue"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import ScoreGauge from "./ScoreGauge.vue"

const props = defineProps<{
  macro: MacroOverview | null
  loading: boolean
  error: string
}>()

const emit = defineEmits<{ retry: []; basis: [] }>()

const sections = computed(() =>
  props.macro
    ? [
        { name: "美国", section: props.macro.usSection },
        { name: "中国", section: props.macro.chinaSection },
        { name: "外盘", section: props.macro.assetsSection },
      ].filter((item) => item.section.status !== "ok")
    : [],
)

function tileDigits(tile: MacroTile): number {
  return tile.code === "CN10Y" ? 3 : tile.code === "FEDFUNDS" ? 2 : 1
}

function factorLines(macro: MacroOverview, which: "growth" | "liquidity"): string {
  return macro[which].factors.map((factor) => `${factor.label}：${factor.score.toFixed(0)}（${factor.raw}）`).join("\n")
}
</script>

<template>
  <PanelCard title="全球宏观概览" info="美国数据来自 FRED 公开 CSV，中国利率与 PMI 来自东方财富数据中心，外盘来自腾讯 / 新浪行情；月度指标有发布滞后，以标注期次为准。">
    <template #icon><el-icon><Odometer /></el-icon></template>
    <template #actions>
      <button type="button" class="link" @click="emit('basis')">计算依据 ›</button>
    </template>

    <StateBlock v-if="loading && !macro" state="loading" :rows="6" message="正在拉取 FRED 与利率数据，服务刚启动时首次约需几秒" />
    <StateBlock v-else-if="error && !macro" state="error" :message="error" @retry="emit('retry')" />
    <template v-else-if="macro">
      <p v-for="item in sections" :key="item.name" class="notice">
        <StatusPill :text="`${item.name}${item.section.status === 'stale' ? '缓存' : '不可用'}`" :tone="item.section.status === 'stale' ? 'warn' : 'danger'" />
        {{ item.section.reason }}
      </p>
      <div class="tiles">
        <div v-for="tile in macro.tiles" :key="tile.code" class="tile">
          <div class="l">{{ tile.label }}</div>
          <div class="v num">{{ tile.value === null ? "—" : `${tile.value.toFixed(tileDigits(tile))}${tile.unit}` }}</div>
          <div class="d">
            {{ tile.changeLabel }}
            <DeltaValue :value="tile.change" :suffix="` ${tile.changeUnit}`" :colored="false" signed :digits="tile.changeUnit === 'bp' ? 0 : 2" size="sm" />
          </div>
          <div class="p">{{ tile.period || "不可用" }}</div>
        </div>
      </div>

      <ScoreGauge
        title="全球经济热度"
        :score="macro.growth.score"
        :label="macro.growth.label"
        :ends="['偏冷', '中性', '偏热']"
        gradient="linear-gradient(90deg,#1d4ed8,#64748b 50%,#ea580c)"
        :info="factorLines(macro, 'growth')"
      />
      <ScoreGauge
        title="流动性环境"
        :score="macro.liquidity.score"
        :label="macro.liquidity.label"
        :ends="['偏紧', '中性', '偏松']"
        gradient="linear-gradient(90deg,#b45309,#64748b 50%,#059669)"
        :info="factorLines(macro, 'liquidity')"
      />

      <div class="assets">
        <div v-for="asset in macro.assets" :key="asset.code" class="asset" :title="asset.quoteTime ? `报价时间 ${asset.quoteTime}` : ''">
          <div class="name">{{ asset.name }}</div>
          <DeltaValue :value="asset.last" :colored="false" :digits="asset.code === 'USDCNY' ? 4 : 2" size="sm" />
          <DeltaValue :value="asset.changePct" suffix="%" size="sm" />
        </div>
      </div>
    </template>
  </PanelCard>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.tile {
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-deep);
}

.tile .l {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tile .v {
  font-size: 21px;
  font-weight: 700;
  color: var(--text-strong);
}

.tile .d {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}

.tile .p {
  font-size: 11px;
  color: var(--faint);
}

.assets {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-top: 14px;
}

.asset {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 6px;
  min-width: 0;
  padding: 5px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  background: var(--panel-deep);
}

.asset .name {
  width: 100%;
  font-size: 12px;
  color: var(--muted);
}

.notice {
  display: flex;
  align-items: center;
  gap: 6px;
}

.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.link:hover {
  color: var(--accent);
}

@media (max-width: 1600px) {
  .tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
