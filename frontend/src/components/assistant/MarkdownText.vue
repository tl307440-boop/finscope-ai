<script setup lang="ts">
/**
 * 助手正文：把 Markdown 子集渲染成段落、小标题、列表和表格。
 * 解析见 api/assistantMarkdown.ts：结果里只有结构化的文字节点，没有 `v-html`。
 */
import { computed } from "vue"
import type { AssistantSource } from "../../api/assistantBlocks"
import { parseMarkdown } from "../../api/assistantMarkdown"
import MarkdownInline from "./MarkdownInline.vue"

const props = withDefaults(defineProps<{ text: string; sources?: AssistantSource[] }>(), {
  sources: () => [],
})

const blocks = computed(() => parseMarkdown(props.text))
</script>

<template>
  <template v-for="(block, index) in blocks" :key="index">
    <h3 v-if="block.kind === 'section'" class="doc-section">{{ block.text }}</h3>

    <component :is="block.ordered ? 'ol' : 'ul'" v-else-if="block.kind === 'list'" class="doc-list">
      <li v-for="(item, at) in block.items" :key="at">
        <MarkdownInline :nodes="item" :sources="sources" />
      </li>
    </component>

    <div v-else-if="block.kind === 'table'" class="md-table" tabindex="0">
      <table>
        <thead>
          <tr>
            <th v-for="(cell, at) in block.header" :key="at" scope="col">
              <MarkdownInline :nodes="cell" :sources="sources" />
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, rowAt) in block.rows" :key="rowAt">
            <td v-for="(cell, at) in row" :key="at">
              <MarkdownInline :nodes="cell" :sources="sources" />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-else class="doc-text">
      <MarkdownInline :nodes="block.inline" :sources="sources" />
    </p>
  </template>
</template>

<style scoped>
.md-table {
  margin: 0 0 10px;
  overflow-x: auto;
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
  border-bottom: 1px solid var(--line-soft);
}

th {
  background: var(--panel-soft);
  color: var(--muted);
  font-weight: 500;
}

tbody tr:last-child td {
  border-bottom: 0;
}
</style>
