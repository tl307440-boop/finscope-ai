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
  <main class="page">
    <section class="panel form-card">
      <h1>登录</h1>
      <p class="muted">金融投研辅助账号。系统不提供交易执行。</p>
      <form class="form" @submit.prevent="submit">
        <label>
          账号
          <input v-model="username" name="username" autocomplete="username" />
        </label>
        <label>
          密码
          <input v-model="password" name="password" type="password" autocomplete="current-password" />
        </label>
        <p v-if="error" class="alert" role="alert">{{ error }}</p>
        <button class="retry" type="submit" :disabled="submitting">登录</button>
      </form>
    </section>
  </main>
</template>
