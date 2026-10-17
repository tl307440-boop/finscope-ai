<script setup lang="ts">
/** 板块轮动（RRG）：GET /api/v1/industry-market/rotation?level=1|2，基于收盘后入库的日线计算。 */
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { sectionBadge } from "../../../api/industryMarket"
import { QUADRANTS, fetchRotation, type RotationView } from "../../../api/marketPulse"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { pctText } from "../industry/chartKit"
import { QUADRANT_HINTS, buildRotationOption, quadrantColor } from "./rotationChart"

const router = useRouter()
const level = ref<"1" | "2">("1")
const view = ref<RotationView | null>(null)
const failure = ref("")
const loading = ref(false)
const tails = ref(true)
const focus = ref("")
const hidden = ref<string[]>([])

const visible = computed(() => (view.value?.series ?? []).filter((item) => !hidden.value.includes(item.quadrant)))
const option = computed(() => buildRotationOption(visible.value, tails.value, focus.value))
const badge = computed(() => (view.value ? sectionBadge(view.value.section) : null))
const groups = computed(() =>
  QUADRANTS.map((quadrant) => ({
    quadrant,
    items: (view.value?.series ?? [])
      .filter((item) => item.quadrant === quadrant)
      .sort((a, b) => b.tail[b.tail.length - 1]!.ratio - a.tail[a.tail.length - 1]!.ratio),
  })),
)
const asOf = computed(() => {
  const first = view.value?.series[0]
  return first ? first.tail[first.tail.length - 1]!.date : ""
})

async function load(): Promise<void> {
  loading.value = true
  failure.value = ""
  focus.value = ""
  try {
    view.value = await fetchRotation(level.value)
    tails.value = level.value === "1"
  } catch (error) {
    view.value = null
    failure.value = error instanceof Error ? error.message : "轮动数据暂时不可用"
  } finally {
    loading.value = false
  }
}

function toggle(quadrant: string): void {
  hidden.value = hidden.value.includes(quadrant)
    ? hidden.value.filter((item) => item !== quadrant)
    : [...hidden.value, quadrant]
}

function onChartClick(info: { data?: unknown }): void {
  const data = info.data as { code?: string } | undefined
  if (data?.code) {
    focus.value = focus.value === data.code ? "" : data.code
  }
}

watch(level, () => void load())
onMounted(() => void load())
</script>

<template>
  <PanelCard
    title="板块轮动图（RRG）"
    info="横轴 RS-Ratio：板块相对沪深300的强弱（100 = 与基准持平）；纵轴 RS-Momentum：这种强弱是在加速还是减速。尾巴是最近几周的轨迹，通常按顺时针 改善→领先→转弱→落后 轮动。"
  >
    <template #actions>
      <StatusPill v-if="badge" :text="badge.text" :tone="badge.tone" dot />
      <el-radio-group v-model="level" size="small">
        <el-radio-button value="1">一级行业</el-radio-button>
        <el-radio-button value="2">二级行业</el-radio-button>
      </el-radio-group>
      <el-switch v-model="tails" size="small" active-text="轨迹" />
    </template>

    <StateBlock v-if="loading && !view" state="loading" :rows="6" />
    <StateBlock v-else-if="failure" state="error" :message="failure" @retry="load" />
    <StateBlock
      v-else-if="view && view.section.status === 'unavailable'"
      state="empty"
      :message="view.section.reason || '还没有足够的入库日线，收盘后任务跑过之后再来看'"
    />
    <template v-else-if="view">
      <div class="legend">
        <button
          v-for="quadrant in QUADRANTS"
          :key="quadrant"
          type="button"
          :class="{ off: hidden.includes(quadrant) }"
          :title="QUADRANT_HINTS[quadrant]"
          @click="toggle(quadrant)"
        >
          <i :style="{ background: quadrantColor(quadrant) }" />
          {{ quadrant }} {{ groups.find((group) => group.quadrant === quadrant)?.items.length ?? 0 }}
        </button>
        <span class="muted fine">点圆点可单独查看轨迹；数据截至 {{ asOf }}</span>
      </div>
      <VChart class="rrg" :option="option" autoresize @click="onChartClick" />

      <div class="quadrants">
        <section v-for="group in groups" :key="group.quadrant">
          <h4 :style="{ color: quadrantColor(group.quadrant) }">
            {{ group.quadrant }} <small class="muted">{{ QUADRANT_HINTS[group.quadrant] }}</small>
          </h4>
          <p v-if="!group.items.length" class="muted fine">暂无</p>
          <ul v-else>
            <li v-for="item in group.items" :key="item.code" :class="{ focus: focus === item.code }">
              <button type="button" class="name" @click="focus = focus === item.code ? '' : item.code">{{ item.name }}</button>
              <span class="tabular" :class="(item.changePct ?? 0) >= 0 ? 'up' : 'down'">{{ pctText(item.changePct) }}</span>
              <a class="go" @click="router.push(`/industry/board/${item.code}`)">分析</a>
            </li>
          </ul>
        </section>
      </div>
      <ul class="rules muted">
        <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
      </ul>
    </template>
  </PanelCard>
</template>

<style scoped>
.legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.legend button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.legend button.off {
  opacity: 0.4;
}

.legend i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.rrg {
  width: 100%;
  height: 520px;
}

.quadrants {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 8px;
}

.quadrants h4 {
  margin: 0 0 6px;
  font-size: 13px;
}

.quadrants h4 small {
  font-weight: 400;
  font-size: 11px;
}

.quadrants ul {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 220px;
  overflow: auto;
}

.quadrants li {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
  align-items: center;
  padding: 4px 2px;
  border-top: 1px solid var(--line-soft);
  font-size: 12px;
}

.quadrants li.focus {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}

.quadrants .name {
  padding: 0;
  border: 0;
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.go {
  color: var(--accent);
  cursor: pointer;
}

.up {
  color: var(--rise);
}

.down {
  color: var(--fall);
}

.fine {
  font-size: 12px;
  margin: 0;
}

.rules {
  margin: 12px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
}

@media (max-width: 1100px) {
  .quadrants {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
