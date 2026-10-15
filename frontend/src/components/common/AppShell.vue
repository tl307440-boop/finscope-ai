<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import { useRoute } from "vue-router"
import { applyTheme, readTheme, toggleTheme, type ThemeName } from "../../shell/theme"
import { session } from "../../stores/auth"

const route = useRoute()
const theme = ref<ThemeName>("dark")
const collapsed = ref(false)

const accountLabel = computed(() => session.username ?? "登录")
const accountHref = computed(() => (session.accessToken ? "/settings" : "/login"))

const links = computed(() => {
  const items = [
    { href: "/", label: "运行状态" },
    { href: "/macro", label: "美国宏观" },
    { href: "/quotes", label: "日线" },
    { href: "/filings", label: "SEC 样例" },
    { href: "/metrics", label: "财务试算" },
    { href: "/company", label: "公司研究" },
    { href: "/uploads", label: "上传 PDF" },
    { href: "/assistant", label: "问答" },
    { href: "/library", label: "资料库" },
  ]
  if (session.role === "admin") {
    items.push({ href: "/data-center", label: "数据源" })
  }
  return items
})

function switchTheme(): void {
  theme.value = applyTheme(toggleTheme(theme.value))
}

function switchRail(): void {
  collapsed.value = !collapsed.value
}

onMounted(() => {
  theme.value = applyTheme(readTheme(window.localStorage.getItem("finscope-theme")))
})
</script>

<template>
  <div class="app-shell" :class="{ collapsed }" :data-layout="collapsed ? 'rail' : 'sidebar'">
    <a class="skip-link" href="#main">跳到主要内容</a>
    <aside class="side-nav" aria-label="主导航">
      <div class="brand">
        <strong>FinScope AI</strong>
        <span>不提供交易执行</span>
      </div>
      <nav class="side-links">
        <a
          v-for="item in links"
          :key="item.href"
          :href="item.href"
          :aria-current="route.path === item.href ? 'page' : undefined"
        >
          {{ item.label }}
        </a>
      </nav>
      <p class="legend">
        <span class="rise">涨 +</span>
        <span class="fall">跌 −</span>
      </p>
      <button class="text-button rail-toggle" type="button" @click="switchRail">
        {{ collapsed ? "展开" : "折叠" }}
      </button>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <p class="muted">数据时间以各页查询为准，不是行情实时承诺</p>
        <div class="nav-links">
          <button class="text-button" type="button" @click="switchTheme">
            {{ theme === "dark" ? "浅色" : "深色" }}
          </button>
          <a :href="accountHref">{{ accountLabel }}</a>
        </div>
      </header>
      <router-view />
    </div>
  </div>
</template>
