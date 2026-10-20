<script setup lang="ts">
/**
 * 行内节点：文字、加粗、行内代码、引用角标。
 * 只有文本插值，没有任何 HTML 透传。引用只在对得上已登记来源时才画成角标，
 * 对不上的 `[n]` 按原文显示，不替模型造一个来源。
 */
import { safeUrl, type AssistantSource } from "../../api/assistantBlocks"
import type { Inline } from "../../api/assistantMarkdown"

const props = defineProps<{ nodes: Inline[]; sources: AssistantSource[] }>()

function sourceOf(n: number): AssistantSource | null {
  return props.sources.find((source) => source.n === n) ?? null
}

function label(source: AssistantSource): string {
  const head = [source.publisher, source.title].filter(Boolean).join(" · ")
  const tail = [source.publishedAt, source.verified ? "" : "网页来源，未经核验"].filter(Boolean).join(" · ")
  return tail ? `${head}（${tail}）` : head
}
</script>

<template>
  <template v-for="(node, index) in nodes" :key="index">
    <template v-if="node.kind === 'text'">{{ node.text }}</template>
    <strong v-else-if="node.kind === 'bold'">
      <MarkdownInline :nodes="node.children" :sources="sources" />
    </strong>
    <code v-else-if="node.kind === 'code'">{{ node.text }}</code>
    <template v-else-if="sourceOf(node.n)">
      <a
        v-if="safeUrl(sourceOf(node.n)?.url ?? '')"
        class="cite"
        :href="safeUrl(sourceOf(node.n)?.url ?? '') ?? undefined"
        target="_blank"
        rel="noopener noreferrer"
        :title="label(sourceOf(node.n) as AssistantSource)"
      >{{ node.n }}</a>
      <span v-else class="cite" :title="label(sourceOf(node.n) as AssistantSource)">{{ node.n }}</span>
    </template>
    <template v-else>[{{ node.n }}]</template>
  </template>
</template>

<style scoped>
code {
  padding: 1px 5px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--text) 8%, transparent);
  font-size: 12px;
}

a.cite {
  text-decoration: none;
}
</style>
