<script setup lang="ts">
/**
 * 板块页「研究假设」：GET /api/v1/industry-market/boards/{code}/theses（需登录）。
 * 东财板块与逻辑追踪行业只按名称对应，对应方式随结果显示。
 */
import { computed, onMounted, ref, watch } from "vue"
import { fetchBoardTheses, FetchFailure, type BoardTheses } from "../../../api/industryMarket"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import StatusPill from "../../../components/ui/StatusPill.vue"
import { restoreSession, useSessionStore } from "../../../stores/auth"

const props = defineProps<{ code: string }>()

const STATUS_META: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  has_support: { label: "得到支持", tone: "ok" },
  disputed: { label: "正反并存", tone: "warn" },
  has_challenge: { label: "受到挑战", tone: "danger" },
  open: { label: "证据不足", tone: "muted" },
}

const session = useSessionStore()
const view = ref<BoardTheses | null>(null)
const failure = ref("")
const loading = ref(false)
const needsLogin = ref(false)

const loginHref = computed(() => `/login?redirect=${encodeURIComponent(`/industry/board/${props.code}`)}`)
const thesisCount = computed(() => view.value?.industries.reduce((total, item) => total + item.theses.length, 0) ?? 0)

async function refreshToken(): Promise<string | null> {
  return (await restoreSession()) ? session.accessToken : null
}

async function load(): Promise<void> {
  loading.value = true
  failure.value = ""
  needsLogin.value = false
  try {
    if (!session.accessToken && !(await restoreSession())) {
      needsLogin.value = true
      return
    }
    view.value = await fetchBoardTheses(props.code, session.accessToken, refreshToken)
  } catch (error) {
    if (error instanceof FetchFailure && error.status === 401) {
      needsLogin.value = true
    } else {
      failure.value = error instanceof Error ? error.message : "研究假设暂时读不到"
    }
  } finally {
    loading.value = false
  }
}

function statusOf(status: string) {
  return STATUS_META[status] ?? { label: status, tone: "muted" as const }
}

watch(() => props.code, () => void load())
onMounted(() => void load())
</script>

<template>
  <PanelCard title="研究假设" info="逻辑追踪模块里你为对应行业写下的研究假设，以及截至今天已入库的支持 / 挑战依据条数">
    <StateBlock v-if="loading && !view" state="loading" :rows="3" />
    <StateBlock v-else-if="needsLogin" state="denied" message="研究假设跟随账号保存，登录后可以查看">
      <a class="login" :href="loginHref">前往登录</a>
    </StateBlock>
    <StateBlock v-else-if="failure" state="error" :message="failure" @retry="load" />
    <template v-else-if="view">
      <StateBlock
        v-if="!view.industries.length"
        state="empty"
        :message="`逻辑追踪的行业目录里没有和「${view.boardName}」同名的行业，所以这里没有可关联的研究假设。`"
      />
      <div v-for="industry in view.industries" :key="industry.id" class="industry">
        <header>
          <h4>{{ industry.name }}</h4>
          <StatusPill :text="`按${industry.match}对应`" tone="muted" />
          <RouterLink class="go" :to="`/industry/${industry.id}`">
            {{ industry.theses.length ? "去逻辑追踪查看" : "去写第一条假设" }} →
          </RouterLink>
        </header>
        <p v-if="!industry.theses.length" class="muted fine">还没有为这个行业写研究假设。</p>
        <ul v-else>
          <li v-for="thesis in industry.theses" :key="thesis.id">
            <div class="title">
              <b>{{ thesis.title }}</b>
              <StatusPill :text="statusOf(thesis.status).label" :tone="statusOf(thesis.status).tone" dot />
            </div>
            <p class="muted">{{ thesis.hypothesis }}</p>
            <p class="counts">
              <span class="support">支持 {{ thesis.support }}</span>
              <span class="challenge">挑战 {{ thesis.challenge }}</span>
              <span class="muted">{{ thesis.rationale }}</span>
            </p>
          </li>
        </ul>
      </div>
      <p v-if="view.industries.length" class="muted fine">共 {{ thesisCount }} 条假设。</p>
      <ul class="rules muted">
        <li v-for="rule in view.rules" :key="rule">{{ rule }}</li>
      </ul>
    </template>
  </PanelCard>
</template>

<style scoped>
.industry + .industry {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--line-soft);
}

header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

h4 {
  margin: 0;
  font-size: 15px;
}

.go,
.login {
  margin-left: auto;
  color: var(--accent);
  font-size: 13px;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

li {
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-raise);
}

.title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

li p {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.counts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.support {
  color: var(--ok);
}

.challenge {
  color: var(--danger);
}

.fine {
  margin: 10px 0 0;
  font-size: 12px;
}

.rules {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.7;
}
</style>
