<script setup lang="ts">
/**
 * 全局搜索（规格书 §1.2）：
 * 支持 A股代码（600519.SH）、名称、美国 ticker 和公司别名；
 * 展示下拉建议、交易所、代码与名称；无匹配时显示“暂无该证券”。
 */
import { computed, ref } from "vue"
import { useRouter } from "vue-router"
import {
  describeHit,
  routeForHit,
  searchInstruments,
  type InstrumentHit,
} from "../../api/instruments"

const router = useRouter()

const keyword = ref("")
const hits = ref<InstrumentHit[]>([])
const loading = ref(false)
const emptyMessage = ref("")
const failed = ref("")

const placeholder = computed(() => emptyMessage.value || "搜索公司 / 代码，如 600519、茅台、AAPL")

async function run(query: string): Promise<void> {
  const trimmed = query.trim()
  failed.value = ""
  if (trimmed.length === 0) {
    hits.value = []
    emptyMessage.value = ""
    return
  }
  loading.value = true
  try {
    const result = await searchInstruments(trimmed, undefined, 10)
    hits.value = result.results
    emptyMessage.value = result.results.length === 0 ? (result.emptyMessage ?? "暂无该证券") : ""
  } catch (caught) {
    hits.value = []
    emptyMessage.value = ""
    failed.value = caught instanceof Error ? caught.message : "搜索失败"
  } finally {
    loading.value = false
  }
}

function open(code: string): void {
  const hit = hits.value.find((item) => item.canonicalCode === code)
  if (!hit) {
    return
  }
  keyword.value = ""
  hits.value = []
  emptyMessage.value = ""
  void router.push(routeForHit(hit))
}
</script>

<template>
  <div class="global-search">
    <el-select
      v-model="keyword"
      class="global-search__input"
      filterable
      remote
      clearable
      reserve-keyword
      :remote-method="run"
      :loading="loading"
      :placeholder="placeholder"
      :no-data-text="emptyMessage || '暂无该证券'"
      :fit-input-width="true"
      size="default"
      aria-label="全局搜索"
      @change="open"
    >
      <el-option
        v-for="hit in hits"
        :key="hit.id"
        :label="describeHit(hit)"
        :value="hit.canonicalCode"
      >
        <span class="hit-exchange">{{ hit.exchange }}</span>
        <span class="hit-code">{{ hit.displaySymbol }}</span>
        <span class="hit-name">{{ hit.nameCn || hit.nameEn }}</span>
      </el-option>
    </el-select>
    <p v-if="failed" class="global-search__error" role="alert">{{ failed }}</p>
  </div>
</template>

<style scoped>
.global-search {
  min-width: 260px;
  max-width: 460px;
  flex: 1 1 300px;
}

.global-search__input {
  width: 100%;
}

.global-search__input :deep(.el-select__wrapper) {
  min-height: 36px;
  border-radius: 8px;
  background: rgb(96 140 220 / 7%);
  box-shadow: 0 0 0 1px var(--line) inset;
}

.global-search__input :deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px var(--accent) inset, 0 0 0 4px rgb(59 130 246 / 14%);
}

.global-search__error {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--danger);
}

.hit-exchange {
  display: inline-block;
  min-width: 54px;
  color: var(--muted);
  font-size: 12px;
}

.hit-code {
  display: inline-block;
  min-width: 92px;
  font-variant-numeric: tabular-nums;
}

.hit-name {
  color: var(--text);
}
</style>
