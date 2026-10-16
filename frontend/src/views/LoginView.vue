<script setup lang="ts">
import { ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import { ApiError } from "../api/client"
import { login, safeRedirect } from "../stores/auth"

const route = useRoute()
const router = useRouter()
const username = ref("")
const password = ref("")
const error = ref("")
const submitting = ref(false)

async function submit(): Promise<void> {
  error.value = ""
  if (username.value.trim().length < 3 || password.value.length === 0) {
    error.value = "请输入账号和密码"
    password.value = ""
    return
  }
  submitting.value = true
  const secret = password.value
  password.value = ""
  try {
    await login(username.value.trim(), secret)
    await router.push(safeRedirect(route.query.redirect))
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-card panel">
      <aside class="brand-side" aria-hidden="true">
        <div class="brand-block">
          <svg class="brand-mark" viewBox="0 0 32 32">
            <path d="M6 24 L13 15 L18 19 L26 8" fill="none" stroke="#22d3ee" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
            <circle cx="26" cy="8" r="3" fill="#60a5fa" />
          </svg>
          <div class="brand-text">
            <strong>FinScope <b>AI</b></strong>
            <span>智能投研 · 洞察未来</span>
          </div>
        </div>
        <ul class="caps">
          <li><i />A 股日线、FRED 宏观与官方公告统一入库</li>
          <li><i />按历史时点回看，只用当时已公布的数据</li>
          <li><i />研究结论附原文出处，可追溯可复核</li>
        </ul>
        <p class="disclaimer">系统不提供交易执行，也不构成投资建议。</p>
      </aside>

      <form class="form" @submit.prevent="submit">
        <h1>登录账号</h1>
        <p class="muted intro">使用管理员分配的投研账号登录</p>
        <label>
          账号
          <input v-model="username" name="username" autocomplete="username" placeholder="请输入账号" />
        </label>
        <label>
          密码
          <input
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </label>
        <p v-if="error" class="alert" role="alert">{{ error }}</p>
        <button class="primary-button" type="submit" :disabled="submitting">
          {{ submitting ? "登录中…" : "登录" }}
        </button>
        <p class="foot muted">数据来源为公开渠道；行情存在延迟，仅供参考。</p>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  place-items: center;
  min-height: calc(100vh - var(--topbar));
  padding: 24px;
}

.login-card {
  width: min(860px, 100%);
  display: grid;
  grid-template-columns: 1fr 1fr;
  padding: 0;
  overflow: hidden;
}

.brand-side {
  position: relative;
  display: grid;
  align-content: space-between;
  gap: 28px;
  padding: 34px 32px;
  background:
    radial-gradient(120% 80% at 0% 0%, rgb(37 99 235 / 32%), transparent 60%),
    radial-gradient(80% 60% at 100% 100%, rgb(34 211 238 / 14%), transparent 70%),
    var(--panel-deep);
  border-right: 1px solid var(--line);
}

.brand-side::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image: radial-gradient(var(--grid-dot) 1px, transparent 1px);
  background-size: 18px 18px;
  mask-image: linear-gradient(180deg, #000, transparent 85%);
  pointer-events: none;
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  width: 44px;
  height: 44px;
  padding: 6px;
  border-radius: 11px;
  background: rgb(37 99 235 / 22%);
  box-shadow: 0 0 0 1px rgb(96 165 250 / 35%) inset, 0 10px 26px -12px rgb(59 130 246 / 90%);
}

.brand-text {
  display: grid;
}

.brand-text strong {
  font-size: 22px;
  color: var(--text-strong);
}

.brand-text b {
  color: var(--cyan);
}

.brand-text span {
  font-size: 13px;
  letter-spacing: 0.12em;
  color: var(--muted);
}

.caps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 14px;
  font-size: 14px;
  color: var(--text);
}

.caps li {
  display: flex;
  align-items: center;
  gap: 10px;
}

.caps i {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--cyan);
  box-shadow: 0 0 10px var(--cyan);
}

.disclaimer {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
}

.form {
  display: grid;
  align-content: center;
  gap: 14px;
  padding: 34px 32px;
}

.form h1 {
  margin: 0;
  font-size: 22px;
  color: var(--text-strong);
}

.intro {
  margin: -6px 0 6px;
  font-size: 13px;
  line-height: 1.6;
}

@media (max-width: 760px) {
  .login-card {
    grid-template-columns: 1fr;
  }

  .brand-side {
    display: none;
  }
}

.primary-button {
  width: 100%;
  padding: 10px 16px;
  border: 0;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, #2563eb, #3b82f6);
  box-shadow: 0 10px 24px -12px rgb(37 99 235 / 90%);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.primary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.foot {
  margin: 4px 0 0;
  font-size: 13px;
  text-align: center;
}
</style>
