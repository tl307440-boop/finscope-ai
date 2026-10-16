<script setup lang="ts">
/**
 * 密钥与数据源配置（仅管理员）：在界面里填 Tiingo / FRED / SEC / 大模型等配置，
 * 不用再改服务器上的 .env。密钥加密后存库，页面只显示末四位；输入框永远不回填明文。
 */
import { computed, onMounted, reactive, ref } from "vue"
import { ApiError } from "../../api/client"
import {
  clearIntegration,
  fetchIntegrations,
  groupItems,
  saveIntegration,
  sourceText,
  testIntegration,
  type IntegrationItem,
} from "../../api/integrations"
import { formatShanghai } from "../../api/health"
import { restoreSession, useSessionStore } from "../../stores/auth"
import PanelCard from "./PanelCard.vue"
import StateBlock from "./StateBlock.vue"
import StatusPill from "./StatusPill.vue"

const session = useSessionStore()
const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const items = ref<IntegrationItem[]>([])
const notice = ref("")
const drafts = reactive<Record<string, string>>({})
const busy = reactive<Record<string, string>>({})
const notes = reactive<Record<string, { ok: boolean; text: string }>>({})

const groups = computed(() => groupItems(items.value))

function refresh(): () => Promise<string | null> {
  return async () => ((await restoreSession()) ? session.accessToken : null)
}

function replace(next: IntegrationItem | null): void {
  if (!next) {
    return
  }
  items.value = items.value.map((item) => (item.key === next.key ? next : item))
  drafts[next.key] = next.secret || next.kind === "bool" ? "" : next.preview
}

function describe(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
}

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const list = await fetchIntegrations(session.accessToken, refresh())
    items.value = list.items
    notice.value = list.notice
    for (const item of list.items) {
      drafts[item.key] = item.secret || item.kind === "bool" ? "" : item.preview
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = describe(caught)
    phase.value = "error"
  }
}

async function run(item: IntegrationItem, action: string, task: () => Promise<void>): Promise<void> {
  busy[item.key] = action
  delete notes[item.key]
  try {
    await task()
  } catch (caught) {
    notes[item.key] = { ok: false, text: describe(caught) }
  } finally {
    delete busy[item.key]
  }
}

function draftOf(item: IntegrationItem): string {
  return (drafts[item.key] ?? "").trim()
}

async function save(item: IntegrationItem, forced?: string): Promise<void> {
  const value = forced ?? draftOf(item)
  if (!value) {
    notes[item.key] = { ok: false, text: "请先填写内容" }
    return
  }
  await run(item, "save", async () => {
    replace(await saveIntegration(item.key, value, session.accessToken, refresh()))
    notes[item.key] = { ok: true, text: "已保存，几秒内对所有后台进程生效" }
  })
}

async function clear(item: IntegrationItem): Promise<void> {
  await run(item, "clear", async () => {
    replace(await clearIntegration(item.key, session.accessToken, refresh()))
    notes[item.key] = { ok: true, text: "已清除，退回服务器环境变量里的值" }
  })
}

async function test(item: IntegrationItem): Promise<void> {
  await run(item, "test", async () => {
    const candidate = draftOf(item)
    const sameAsShown = !item.secret && candidate === item.preview
    const outcome = await testIntegration(
      item.key,
      candidate && !sameAsShown ? candidate : null,
      session.accessToken,
      refresh(),
    )
    notes[item.key] = { ok: outcome.ok, text: outcome.message }
  })
}

function toggle(item: IntegrationItem, on: boolean): Promise<void> {
  return save(item, on ? "true" : "false")
}

function tone(item: IntegrationItem): "ok" | "warn" | "muted" {
  if (item.problem) {
    return "warn"
  }
  return item.configured ? "ok" : "muted"
}

function stamp(item: IntegrationItem): string {
  if (!item.updatedAt) {
    return ""
  }
  const who = item.updatedBy ? `${item.updatedBy} ` : ""
  return `${who}${formatShanghai(item.updatedAt)} 更新`
}

onMounted(load)
</script>

<template>
  <PanelCard title="密钥与数据源配置" info="在这里填写的值加密保存在数据库里，优先于服务器环境变量，几秒内对所有后台进程生效">
    <template #actions>
      <button class="ghost-btn" type="button" @click="load">刷新</button>
    </template>
    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />
    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取配置"
      :message="failure"
      @retry="load"
    />
    <div v-else class="integrations">
      <p class="notice">{{ notice }}</p>
      <section v-for="group in groups" :key="group.group" class="group">
        <h4>{{ group.group }}</h4>
        <div v-for="item in group.items" :key="item.key" class="row" :data-key="item.key">
          <div class="meta">
            <div class="title">
              <span class="label">{{ item.label }}</span>
              <StatusPill :text="sourceText(item)" :tone="tone(item)" dot />
              <span v-if="item.configured && item.secret" class="preview">{{ item.preview }}</span>
            </div>
            <p v-if="item.help" class="help">
              {{ item.help }}
              <a v-if="item.helpUrl" :href="item.helpUrl" target="_blank" rel="noopener noreferrer">去获取</a>
            </p>
            <p v-if="item.problem" class="problem">{{ item.problem }}</p>
            <p v-if="stamp(item)" class="stamp">{{ stamp(item) }}</p>
          </div>

          <div v-if="item.kind === 'bool'" class="edit">
            <el-switch
              :model-value="item.preview === 'true'"
              :loading="busy[item.key] === 'save'"
              :aria-label="item.label"
              @change="(value: unknown) => toggle(item, value === true)"
            />
          </div>
          <div v-else class="edit">
            <el-input
              v-model="drafts[item.key]"
              :type="item.secret ? 'password' : 'text'"
              :show-password="item.secret"
              :placeholder="item.secret && item.configured ? '已配置，留空表示不修改' : item.placeholder || '请输入'"
              :aria-label="item.label"
              autocomplete="off"
              clearable
              @keyup.enter="save(item)"
            />
            <div class="buttons">
              <el-button size="small" :loading="busy[item.key] === 'test'" @click="test(item)">测试连接</el-button>
              <el-button
                size="small"
                type="primary"
                :loading="busy[item.key] === 'save'"
                @click="save(item)"
              >保存</el-button>
              <el-button
                v-if="item.source === 'database'"
                size="small"
                text
                :loading="busy[item.key] === 'clear'"
                @click="clear(item)"
              >清除</el-button>
            </div>
          </div>
          <p
            v-if="notes[item.key]"
            class="note"
            :class="notes[item.key]?.ok ? 'ok' : 'bad'"
            role="status"
          >{{ notes[item.key]?.text }}</p>
        </div>
      </section>
    </div>
  </PanelCard>
</template>

<style scoped>
.integrations {
  display: grid;
  gap: 18px;
}
.notice {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}
h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--muted);
}
.group {
  display: grid;
  gap: 4px;
}
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 380px);
  gap: 8px 20px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}
.title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
}
.label {
  font-weight: 600;
  color: var(--text-strong);
}
.preview {
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.help,
.stamp {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--muted);
}
.problem {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--warn, #f59e0b);
}
.edit {
  display: grid;
  gap: 8px;
  align-content: start;
}
.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.note {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
}
.note.ok {
  color: var(--ok, #22c55e);
}
.note.bad {
  color: var(--danger, #ef4444);
}
@media (max-width: 760px) {
  .row {
    grid-template-columns: 1fr;
  }
}
</style>
