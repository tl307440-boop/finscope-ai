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
      <header class="brand-block">
        <span class="brand-mark" aria-hidden="true">FS</span>
        <div class="brand-text">
          <h1>FinScope AI</h1>
          <p>智能投研 · 深度洞察</p>
        </div>
      </header>

      <p class="muted intro">金融投研辅助账号。系统不提供交易执行，也不构成投资建议。</p>

      <form class="form" @submit.prevent="submit">
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
      </form>

      <p class="foot muted">数据来源为公开渠道；行情存在延迟，仅供参考。</p>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
  background: var(--bg);
}

.login-card {
  width: min(420px, 100%);
  display: grid;
  gap: 18px;
  padding: 28px;
}

.brand-block {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: var(--radius-sm);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.brand-text h1 {
  margin: 0;
  font-size: 20px;
  letter-spacing: 0.01em;
}

.brand-text p {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.intro {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.form {
  gap: 14px;
}

.primary-button {
  width: 100%;
  padding: 10px 16px;
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--accent);
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
  margin: 0;
  font-size: 12px;
  text-align: center;
}
</style>
