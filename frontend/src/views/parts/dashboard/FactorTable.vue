<script setup lang="ts">
/** 规则分的逐因子拆解：权重、原始数据、换算公式、得分与贡献（贡献由后端给出）。 */
import type { Factor } from "../../../api/dashboard"

defineProps<{ factors: readonly Factor[] }>()
</script>

<template>
  <table class="dash-table factors">
    <colgroup>
      <col style="width: 150px" /><col style="width: 52px" /><col /><col style="width: 30%" /><col style="width: 56px" /><col style="width: 110px" />
    </colgroup>
    <thead>
      <tr><th>因子</th><th class="r">权重</th><th>原始数据</th><th>换算</th><th class="r">得分</th><th class="r">贡献</th></tr>
    </thead>
    <tbody>
      <tr v-for="factor in factors" :key="factor.code">
        <td class="wrap"><b>{{ factor.label }}</b></td>
        <td class="r num">{{ (factor.weight * 100).toFixed(0) }}%</td>
        <td class="wrap">{{ factor.raw }}</td>
        <td class="wrap muted">{{ factor.formula }}</td>
        <td class="r num">{{ factor.score.toFixed(1) }}</td>
        <td class="r num">
          <template v-if="factor.contribution !== null">
            <span class="contrib" :style="{ width: `${Math.max(2, factor.contribution * 1.5)}px` }" />
            {{ factor.contribution.toFixed(1) }}
          </template>
          <template v-else>—</template>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped src="./panel.css"></style>
<style scoped>
.factors td.wrap {
  white-space: normal;
  line-height: 1.6;
}

.muted {
  color: var(--muted);
}

.contrib {
  display: inline-block;
  height: 6px;
  margin-right: 4px;
  border-radius: 3px;
  background: var(--accent);
  vertical-align: middle;
}
</style>
