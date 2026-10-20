<script setup lang="ts">
/**
 * 回答引用到的来源清单。网页来源明确标「未经核验」；没有来源就不显示这一块。
 */
import { safeUrl, type AssistantSource } from "../../api/assistantBlocks"

defineProps<{ sources: AssistantSource[] }>()
</script>

<template>
  <section v-if="sources.length" class="sources" aria-label="来源">
    <h4>来源</h4>
    <ol>
      <li v-for="source in sources" :key="source.n" :value="source.n">
        <a
          v-if="safeUrl(source.url)"
          :href="safeUrl(source.url) ?? undefined"
          target="_blank"
          rel="noopener noreferrer"
        >{{ source.title || source.publisher }}</a>
        <span v-else>{{ source.title || source.publisher }}</span>
        <span v-if="source.title && source.publisher" class="meta"> · {{ source.publisher }}</span>
        <span v-if="source.publishedAt" class="meta"> · {{ source.publishedAt }}</span>
        <span v-if="!source.verified" class="warn"> · 网页来源，未经核验</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.sources {
  margin-top: 10px;
  font-size: 12.5px;
}

h4 {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

ol {
  margin: 0;
  padding-left: 22px;
  display: grid;
  gap: 2px;
}

.meta {
  color: var(--muted);
}

.warn {
  color: var(--warn);
}
</style>
