<template>
  <el-drawer
    v-model="linkageStore.seatManagerVisible"
    title="游资/派系席位字典管理（动态持久化配置）"
    size="540px"
    append-to-body
  >
    <div class="seat-drawer-body">
      <!-- 新增或修改游资派系表单 -->
      <div class="seat-form-card">
        <div class="form-card-title">
          {{ editingId ? "编辑游资派系与绑定营业部" : "新增自定义游资/机构席位" }}
        </div>
        <div class="form-grid">
          <div class="form-field">
            <label>游资/派系名称</label>
            <input
              v-model="formName"
              type="text"
              placeholder="如：章盟主 / 作手新一 / 某新锐游资"
              class="seat-input"
            />
          </div>
          <div class="form-field">
            <label>身份标签</label>
            <select v-model="formRole" class="seat-select">
              <option value="知名游资">知名游资</option>
              <option value="量化打板">量化打板</option>
              <option value="外资">外资</option>
              <option value="机构专用">机构专用</option>
              <option value="散户大本营">散户大本营</option>
            </select>
          </div>
        </div>
        <div class="form-field">
          <label>关联常用营业部（多个用分号 ； 或换行分隔，应对游资换席位）</label>
          <textarea
            v-model="formBranches"
            rows="2"
            placeholder="例如：国泰海通南京太平南路；国泰海通南京金融城；中信上海分公司"
            class="seat-textarea"
          />
        </div>
        <div class="form-field">
          <label>操盘风格备注</label>
          <input
            v-model="formStyle"
            type="text"
            placeholder="例如：主封核心高标、大容量趋势主升"
            class="seat-input"
          />
        </div>
        <div class="form-actions">
          <button type="button" class="btn-primary" @click="saveSeatForm">
            {{ editingId ? "保存席位修改" : "+ 添加至关注名单" }}
          </button>
          <button
            v-if="editingId"
            type="button"
            class="btn-secondary"
            @click="resetForm"
          >
            取消编辑
          </button>
          <button
            type="button"
            class="btn-ghost"
            @click="linkageStore.resetSeatsToDefault()"
          >
            恢复预置21大派系默认配置
          </button>
        </div>
      </div>

      <!-- 搜索过滤 -->
      <div class="seat-search-bar">
        <input
          v-model="searchKeyword"
          type="text"
          placeholder="搜索游资名称或营业部关键字（如：江苏路、拉萨、桑田路、呼家楼）..."
          class="seat-input"
        />
        <span class="seat-count-stat">
          共 {{ filteredGroups.length }} 个派系
        </span>
      </div>

      <!-- 席位列表 -->
      <div class="seat-group-list">
        <div
          v-for="group in filteredGroups"
          :key="group.id"
          class="seat-group-item"
          :class="{ unwatched: !isWatched(group) }"
        >
          <div class="sg-top">
            <div class="sg-title">
              <input
                type="checkbox"
                :checked="isWatched(group)"
                title="勾选后在龙虎榜分析中跟踪该席位"
                @change="linkageStore.toggleSeatWatched(group.id)"
              />
              <span class="sg-name">{{ group.name }}</span>
              <span class="sg-role">{{ group.category }}</span>
              <span class="sg-style">{{ group.styleTag }}</span>
            </div>
            <div class="sg-ops">
              <button type="button" class="op-link" @click="startEdit(group)">
                改席位
              </button>
              <button
                type="button"
                class="op-link danger"
                @click="linkageStore.deleteSeatGroup(group.id)"
              >
                删除
              </button>
            </div>
          </div>

          <div class="sg-branches">
            <span
              v-for="br in group.branches"
              :key="br"
              class="branch-chip"
            >
              {{ br }}
              <i
                class="branch-del"
                title="移除该营业部"
                @click="linkageStore.removeBranchFromSeat(group.id, br)"
              >
                ×
              </i>
            </span>
            <div class="quick-add-branch">
              <input
                v-model="quickBranchInputs[group.id]"
                type="text"
                placeholder="+ 新席位营业部"
                class="mini-branch-input"
                @keyup.enter="handleQuickAddBranch(group.id)"
              />
              <button
                type="button"
                class="mini-add-btn"
                @click="handleQuickAddBranch(group.id)"
              >
                绑定
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue"
import { useMarketLinkageStore } from "../../../stores/marketLinkage"
import { isWatched, type HotMoneySeatGroup, type SeatCategory } from "../../../config/hotMoneySeatsConfig"

const linkageStore = useMarketLinkageStore()

const searchKeyword = ref("")
const editingId = ref("")
const formName = ref("")
const formRole = ref<SeatCategory>("知名游资")
const formBranches = ref("")
const formStyle = ref("")
const quickBranchInputs = reactive<Record<string, string>>({})

const filteredGroups = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return linkageStore.seatGroups
  return linkageStore.seatGroups.filter(
    (g) =>
      g.name.toLowerCase().includes(kw) ||
      g.category.toLowerCase().includes(kw) ||
      g.branches.some((b) => b.toLowerCase().includes(kw))
  )
})

function resetForm(): void {
  editingId.value = ""
  formName.value = ""
  formRole.value = "知名游资"
  formBranches.value = ""
  formStyle.value = ""
}

function startEdit(group: HotMoneySeatGroup): void {
  editingId.value = group.id
  formName.value = group.name
  formRole.value = group.category
  formBranches.value = group.branches.join("；")
  formStyle.value = group.styleTag
}

function saveSeatForm(): void {
  const branches = formBranches.value
    .split(/[;；\n,，]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (!formName.value.trim() || branches.length === 0) return

  linkageStore.addOrUpdateSeatGroup({
    id: editingId.value || undefined,
    name: formName.value,
    category: formRole.value,
    styleTag: formStyle.value,
    branches,
    watched: true,
  })
  resetForm()
}

function handleQuickAddBranch(seatId: string): void {
  const val = quickBranchInputs[seatId]?.trim()
  if (!val) return
  linkageStore.addBranchToSeat(seatId, val)
  quickBranchInputs[seatId] = ""
}
</script>

<style scoped>
.seat-drawer-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: 12.5px;
}

.seat-form-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.form-card-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.form-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 8px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-field label {
  font-size: 11px;
  color: var(--text-secondary);
}

.seat-input,
.seat-select,
.seat-textarea {
  width: 100%;
  padding: 6px 9px;
  border-radius: 6px;
  border: 1px solid var(--border-strong);
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 12px;
}

.form-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.btn-primary {
  padding: 6px 12px;
  border-radius: 6px;
  border: none;
  background: var(--accent);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.btn-secondary,
.btn-ghost {
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--border-strong);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
}

.seat-search-bar {
  display: flex;
  align-items: center;
  gap: 10px;
}

.seat-count-stat {
  font-size: 11.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

.seat-group-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.seat-group-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 9px 11px;
  background: var(--bg-card);
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.seat-group-item.unwatched {
  opacity: 0.6;
}

.sg-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sg-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sg-name {
  font-weight: 700;
  color: var(--text-primary);
}

.sg-role {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(246, 70, 93, 0.12);
  color: var(--rise);
}

.sg-style {
  font-size: 11px;
  color: var(--text-tertiary);
}

.sg-ops {
  display: flex;
  gap: 8px;
}

.op-link {
  border: none;
  background: none;
  font-size: 11.5px;
  color: var(--accent);
  cursor: pointer;
  padding: 0;
}

.op-link.danger {
  color: var(--fall);
}

.sg-branches {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.branch-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  font-size: 11px;
  color: var(--text-secondary);
}

.branch-del {
  cursor: pointer;
  font-style: normal;
  color: var(--text-tertiary);
  font-weight: 700;
}

.branch-del:hover {
  color: var(--rise);
}

.quick-add-branch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.mini-branch-input {
  width: 115px;
  padding: 2px 6px;
  font-size: 11px;
  border-radius: 4px;
  border: 1px dashed var(--border-strong);
  background: var(--bg-surface);
  color: var(--text-primary);
}

.mini-add-btn {
  padding: 2px 6px;
  font-size: 11px;
  border-radius: 4px;
  border: 1px solid var(--border-strong);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
}
</style>
