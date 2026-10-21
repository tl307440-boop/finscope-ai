<script setup lang="ts">
/**
 * 问答会话列表（历史记录）。每个会话是一段独立的对话：模型只看得到本会话里的前文。
 *
 * 只负责显示和收集操作，请求由父组件发：select / create / rename / remove / retry。
 * 改名在原位编辑；删除要再点一次「确认删除」。`locked` 为真（正在生成回答）时不许切换、改名、删除。
 */
import { computed, nextTick, ref } from "vue"
import { cleanTitle, groupSessions, titleProblem, TITLE_MAX, type SessionSummary } from "../../api/assistantSessions"
import { formatMoment } from "../../api/json"
import StateBlock from "../ui/StateBlock.vue"

const props = withDefaults(
  defineProps<{
    sessions: SessionSummary[]
    activeId: string
    phase: "loading" | "ready" | "error"
    /** 正在生成回答：只能看，不能切换、改名、删除。 */
    locked?: boolean
    message?: string
    /** 改名、删除失败时父组件给的原因 */
    actionError?: string
  }>(),
  { locked: false, message: "", actionError: "" },
)

const emit = defineEmits<{
  select: [id: string]
  create: []
  rename: [id: string, title: string]
  remove: [id: string]
  retry: []
}>()

const editingId = ref("")
const draft = ref("")
const draftError = ref("")
const confirmingId = ref("")
const editor = ref<HTMLInputElement[] | null>(null)

const groups = computed(() => groupSessions(props.sessions))

function select(id: string): void {
  if (props.locked || id === props.activeId || editingId.value) {
    return
  }
  emit("select", id)
}

async function startRename(item: SessionSummary): Promise<void> {
  if (props.locked) {
    return
  }
  confirmingId.value = ""
  editingId.value = item.id
  draft.value = item.title
  draftError.value = ""
  await nextTick()
  editor.value?.[0]?.focus()
  editor.value?.[0]?.select()
}

function submitRename(item: SessionSummary): void {
  const problem = titleProblem(draft.value)
  if (problem) {
    draftError.value = problem
    return
  }
  const cleaned = cleanTitle(draft.value)
  editingId.value = ""
  if (cleaned !== item.title) {
    emit("rename", item.id, cleaned)
  }
}

function cancelRename(): void {
  editingId.value = ""
  draftError.value = ""
}

function askRemove(id: string): void {
  if (props.locked) {
    return
  }
  editingId.value = ""
  confirmingId.value = id
}

function confirmRemove(id: string): void {
  confirmingId.value = ""
  emit("remove", id)
}
</script>

<template>
  <nav class="sessions" aria-label="问答记录">
    <header class="sessions-head">
      <h3>问答记录</h3>
      <button type="button" class="new" :disabled="locked || phase !== 'ready'" @click="emit('create')">
        ＋ 新对话
      </button>
    </header>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="message || '问答记录读不出来'" @retry="emit('retry')" />
    <p v-else-if="!sessions.length" class="muted empty" role="status">还没有问答记录。发出第一个问题后，这里会按时间列出每段对话。</p>

    <template v-else>
      <p v-if="locked" class="muted lock-note" role="status">回答生成中，结束后才能切换对话。</p>
      <p v-if="actionError" class="action-error" role="alert">{{ actionError }}</p>
      <section v-for="block in groups" :key="block.group" class="group">
        <h4 class="group-name">{{ block.group }}</h4>
        <ul>
          <li
            v-for="item in block.items"
            :key="item.id"
            class="item"
            :class="{ active: item.id === activeId }"
            :data-session="item.id"
          >
            <form v-if="editingId === item.id" class="rename" @submit.prevent="submitRename(item)">
              <input
                ref="editor"
                v-model="draft"
                class="rename-input"
                :maxlength="TITLE_MAX + 20"
                aria-label="新的标题"
                @keydown.esc.prevent="cancelRename"
              />
              <div class="row-actions">
                <button type="submit" class="link">保存</button>
                <button type="button" class="link" @click="cancelRename">取消</button>
              </div>
              <p v-if="draftError" class="action-error" role="alert">{{ draftError }}</p>
            </form>

            <div v-else-if="confirmingId === item.id" class="confirm" role="alertdialog" :aria-label="`删除「${item.title}」`">
              <p>删除「{{ item.title }}」及其中全部消息？删除后不能恢复。</p>
              <div class="row-actions">
                <button type="button" class="link danger" @click="confirmRemove(item.id)">确认删除</button>
                <button type="button" class="link" @click="confirmingId = ''">取消</button>
              </div>
            </div>

            <template v-else>
              <button
                type="button"
                class="pick"
                :disabled="locked && item.id !== activeId"
                :aria-current="item.id === activeId ? 'true' : undefined"
                @click="select(item.id)"
              >
                <span class="title">{{ item.title }}</span>
                <span class="muted when">{{ formatMoment(item.updatedAt) }}</span>
              </button>
              <div class="row-actions hover">
                <button type="button" class="link" :disabled="locked" :aria-label="`重命名「${item.title}」`" @click="startRename(item)">
                  改名
                </button>
                <button type="button" class="link danger" :disabled="locked" :aria-label="`删除「${item.title}」`" @click="askRemove(item.id)">
                  删除
                </button>
              </div>
            </template>
          </li>
        </ul>
      </section>
    </template>
  </nav>
</template>

<style scoped>
.sessions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.sessions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sessions-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}

.new {
  padding: 4px 10px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
  cursor: pointer;
}

.new:disabled,
.pick:disabled,
.link:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.empty,
.lock-note {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.action-error {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--warn);
}

.group-name {
  margin: 6px 0 4px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
}

.group ul {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 2px;
}

.item {
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  padding: 4px 6px;
}

.item.active {
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
  background: var(--accent-soft);
}

.pick {
  display: grid;
  width: 100%;
  gap: 2px;
  padding: 2px 0;
  border: none;
  background: none;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

.item.active .title {
  color: var(--text-strong);
  font-weight: 600;
}

.when {
  font-size: 11px;
}

.row-actions {
  display: flex;
  gap: 10px;
}

.row-actions.hover {
  opacity: 0.55;
}

.item:hover .row-actions.hover,
.item:focus-within .row-actions.hover {
  opacity: 1;
}

.link {
  padding: 0;
  border: none;
  background: none;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.link:hover,
.link:focus-visible {
  color: var(--accent);
}

.link.danger:hover,
.link.danger:focus-visible {
  color: var(--danger);
}

.rename-input {
  width: 100%;
  box-sizing: border-box;
  padding: 4px 6px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text-strong);
  font-size: 13px;
}

.confirm p {
  margin: 0 0 4px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
}
</style>
