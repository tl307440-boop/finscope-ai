<script setup lang="ts">
import { onMounted, ref } from "vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../api/client"
import { restoreSession, session } from "../stores/auth"

interface ProviderRow {
  code: string
  credentialRequired: boolean
  credentialPresent: boolean
}

const phase = ref<"loading" | "ready" | "denied" | "error">("loading")
const rows = ref<ProviderRow[]>([])
const failure = ref("无法读取数据源状态")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/providers",
      { method: "GET" },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    if (response.status === 403) {
      phase.value = "denied"
      return
    }
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const providers = Array.isArray(data?.providers) ? data.providers : []
    rows.value = providers.flatMap((item) => {
      const record = asRecord(item)
      const code = asString(record?.code)
      if (!code) {
        return []
      }
      return [
        {
          code,
          credentialRequired: record?.credential_required === true,
          credentialPresent: record?.credential_present === true,
        },
      ]
    })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function label(row: ProviderRow): string {
  if (!row.credentialRequired) {
    return "不需要密钥"
  }
  return row.credentialPresent ? "已提供密钥" : "未提供密钥"
}

onMounted(() => {
  void load()
})

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取数据源状态</section>
    <section v-else-if="phase === 'denied'" class="panel" role="alert">
      <h1>没有访问权限</h1>
      <p>研究者不能查看管理接口。</p>
    </section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取数据源状态</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>
    <section v-else class="panel">
      <h1>数据源配置</h1>
      <p class="muted">这里只显示密钥是否已经配置，不展示密钥，也不表示行情已经同步。</p>
      <ul v-if="rows.length" class="provider-list">
        <li v-for="row in rows" :key="row.code">{{ row.code }} · {{ label(row) }}</li>
      </ul>
      <p v-else>尚未接入</p>
    </section>
  </main>
</template>
