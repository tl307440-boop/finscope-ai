<script setup lang="ts">
import { computed } from "vue"
import {
  PROFILE_LABEL,
  RATING_LABEL,
  ratingTone,
  signedPercent,
  type ProposalItem,
  type RiskItem,
} from "../../../api/simulation"

const props = defineProps<{ proposals: ProposalItem[]; risks: RiskItem[]; aborted: boolean }>()

const byRevision = computed(() =>
  props.proposals.map((proposal) => ({
    proposal,
    critiques: props.risks.filter((item) => item.revision === proposal.revision),
  })),
)

function verdict(item: RiskItem): { text: string; cls: string } {
  if (item.vetoReason) {
    return { text: "一票否决", cls: "veto" }
  }
  if (item.approved === true) {
    return { text: "通过", cls: "pass" }
  }
  return { text: "有保留", cls: "hold" }
}
</script>

<template>
  <div class="committee">
    <ol class="chain" aria-label="提案修正链">
      <li v-for="(proposal, index) in proposals" :key="proposal.revision" class="link">
        <span class="badge" :class="ratingTone(proposal.displayRating)">
          {{ proposal.displayRating || "—" }}
          <small>{{ RATING_LABEL[proposal.displayRating] ?? "" }}</small>
        </span>
        <span class="caption">{{ index === 0 ? "初始提案" : `第 ${proposal.revision} 次修正` }}</span>
        <span v-if="index < proposals.length - 1" class="arrow" aria-hidden="true">→</span>
      </li>
      <li v-if="aborted" class="aborted">保守风控连续否决，已强制转为防御方案</li>
    </ol>

    <section v-for="group in byRevision" :key="group.proposal.revision" class="revision">
      <p class="rationale">
        <span class="tag">{{ group.proposal.revision === 0 ? "初始提案" : `第 ${group.proposal.revision} 次修正` }}</span>
        {{ group.proposal.rationale }}
        <span class="meta">参考配置 {{ group.proposal.allocation }} · 关注 {{ group.proposal.horizonDays ?? "—" }} 天</span>
      </p>
      <div class="critiques">
        <article v-for="item in group.critiques" :key="item.profile" class="critique" :class="verdict(item).cls">
          <header>
            <span>{{ PROFILE_LABEL[item.profile] }}</span>
            <span class="verdict">{{ verdict(item).text }}</span>
          </header>
          <p>{{ item.content }}</p>
          <dl>
            <div>
              <dt>压力情景</dt>
              <dd>{{ item.scenario ?? "—" }}</dd>
            </div>
            <div>
              <dt>估算回撤</dt>
              <dd class="tabular">{{ signedPercent(item.drawdown, 1) }}</dd>
            </div>
          </dl>
          <ul v-if="item.mitigations.length">
            <li v-for="text in item.mitigations" :key="text">{{ text }}</li>
          </ul>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.committee {
  display: grid;
  gap: 14px;
}

.chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.badge {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  font-family: var(--font-num);
  font-weight: 600;
  font-size: 14px;
}

.badge small {
  font-family: inherit;
  font-weight: 400;
  font-size: 12px;
  color: var(--muted);
}

.badge.rise {
  color: var(--rise);
  border-color: color-mix(in srgb, var(--rise) 40%, transparent);
}

.badge.fall {
  color: var(--fall);
  border-color: color-mix(in srgb, var(--fall) 40%, transparent);
}

.caption {
  font-size: 12px;
  color: var(--muted);
}

.arrow {
  color: var(--faint);
}

.aborted {
  font-size: 12px;
  color: var(--warn);
}

.revision {
  display: grid;
  gap: 10px;
}

.rationale {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.tag {
  margin-right: 6px;
  color: var(--muted);
}

.meta {
  margin-left: 8px;
  color: var(--muted);
  font-size: 12px;
}

.critiques {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.critique {
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line-soft);
  background: var(--panel-deep);
  font-size: 13px;
}

.critique header {
  display: flex;
  justify-content: space-between;
  color: var(--text-strong);
}

.verdict {
  font-size: 12px;
}

.pass .verdict {
  color: var(--ok);
}

.hold .verdict {
  color: var(--muted);
}

.veto {
  border-color: color-mix(in srgb, var(--warn) 45%, transparent);
}

.veto .verdict {
  color: var(--warn);
  font-weight: 600;
}

.critique p {
  margin: 0;
  line-height: 1.55;
  color: var(--text);
}

dl {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin: 0;
}

dt {
  font-size: 11px;
  color: var(--muted);
}

dd {
  margin: 0;
  font-size: 12px;
  color: var(--text);
}

.critique ul {
  margin: 0;
  padding-left: 16px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 1000px) {
  .critiques {
    grid-template-columns: 1fr;
  }
}
</style>
