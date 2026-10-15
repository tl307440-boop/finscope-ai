<script setup lang="ts">
/**
 * 页面底部数据来源条（效果图每页都有）。
 * 作用不止装饰：规格书 §1.2 要求页面能表达 stale 状态，这里给出数据截至时间与延迟说明。
 */
withDefaults(
  defineProps<{
    /** 数据截至时间，已格式化好的字符串 */
    asOf?: string
    /** 后端 meta.source 里的数据源标识 */
    sources?: readonly string[]
    /** 行情延迟说明 */
    latency?: string
    /** 后端 meta.stale */
    stale?: boolean
    /** 补充说明，例如「部分数据为季调」 */
    note?: string
  }>(),
  { asOf: "", sources: () => [], latency: "", stale: false, note: "" },
)
</script>

<template>
  <footer class="source-footer">
    <span class="as-of">数据截至：{{ asOf || "—" }}</span>
    <span v-if="sources.length" class="sources">
      数据来源：
      <em v-for="item in sources" :key="item">{{ item }}</em>
    </span>
    <span v-if="latency" class="latency">行情延迟：{{ latency }}</span>
    <span v-if="note" class="note">注：{{ note }}</span>
    <span class="spacer" />
    <span v-if="stale" class="stale"><i aria-hidden="true" />数据可能过期</span>
    <span v-else class="ok"><i aria-hidden="true" />数据正常</span>
  </footer>
</template>

<style scoped>
.source-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 20px;
  padding: 10px 4px 0;
  border-top: 1px solid var(--line);
  font-size: 12px;
  color: var(--muted);
}

.sources em {
  font-style: normal;
  margin-right: 8px;
  color: var(--text);
}

.spacer {
  flex: 1;
}

.stale,
.ok {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.stale i,
.ok i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.stale {
  color: var(--warn);
}

.stale i {
  background: var(--warn);
}

.ok {
  color: var(--ok);
}

.ok i {
  background: var(--ok);
}
</style>
