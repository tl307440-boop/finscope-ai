<script setup lang="ts">
import type { DebateItem } from "../../../api/simulation"
import CitationChips from "./CitationChips.vue"

defineProps<{ debates: DebateItem[]; memo: string }>()

function split(text: string): { claim: string; rebuttal: string } {
  const [claim, ...rest] = text.split("\n")
  return { claim: claim ?? "", rebuttal: rest.join(" ") }
}
</script>

<template>
  <div class="studio">
    <p v-if="!debates.length" class="empty">这次推演没有辩论记录。</p>
    <section v-for="item in debates" :key="item.round" class="round">
      <header class="round-head">
        <span class="round-no tabular">第 {{ item.round }} 轮</span>
        <span v-if="item.converged" class="converged">已收敛</span>
      </header>
      <div class="sides">
        <div class="side bull">
          <span class="who">多头研究员</span>
          <p>{{ split(item.bull).claim }}</p>
          <p v-if="split(item.bull).rebuttal" class="rebuttal">{{ split(item.bull).rebuttal }}</p>
          <CitationChips :items="item.bullCitations" />
        </div>
        <div class="side bear">
          <span class="who">空头研究员</span>
          <p>{{ split(item.bear).claim }}</p>
          <p v-if="split(item.bear).rebuttal" class="rebuttal">{{ split(item.bear).rebuttal }}</p>
          <CitationChips :items="item.bearCitations" />
        </div>
      </div>
      <p class="lead"><span class="who">主导研究员</span>{{ item.arbitration }}</p>
      <ul v-if="item.unsupported.length" class="unsupported">
        <li v-for="claim in item.unsupported" :key="claim">已剔除（无引用）：{{ claim }}</li>
      </ul>
    </section>
    <p v-if="memo" class="memo">{{ memo }}</p>
  </div>
</template>

<style scoped>
.studio {
  display: grid;
  gap: 14px;
}

.empty {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.round {
  display: grid;
  gap: 10px;
}

.round-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}

.round-no {
  color: var(--text);
  font-weight: 600;
}

.converged {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent);
}

.sides {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.side {
  display: grid;
  align-content: start;
  gap: 6px;
  padding: 12px;
  border-radius: var(--radius-sm);
  background: var(--panel-deep);
}

.bull {
  --side: var(--rise);
  background: color-mix(in srgb, var(--rise) 6%, var(--panel-deep));
}

.bear {
  --side: var(--fall);
  background: color-mix(in srgb, var(--fall) 6%, var(--panel-deep));
}

.who {
  font-size: 12px;
  color: var(--muted);
}

.side .who::before {
  content: "";
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 6px;
  border-radius: 50%;
  background: var(--side);
  vertical-align: 1px;
}

.side p,
.lead {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.rebuttal {
  color: var(--muted) !important;
}

.lead {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  border: 1px dashed var(--line);
}

.unsupported {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--warn);
}

.memo {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

@media (max-width: 900px) {
  .sides {
    grid-template-columns: 1fr;
  }
}
</style>
