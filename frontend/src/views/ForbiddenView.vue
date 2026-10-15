<script setup lang="ts">
import StateBlock from "../components/ui/StateBlock.vue"
</script>

<template>
  <main class="page">
    <StateBlock
      state="denied"
      title="没有访问权限"
      message="当前账号不能打开管理功能。如需数据源与同步任务管理，请联系管理员分配权限。"
    >
      <router-link class="text-button" to="/settings">返回账号设置</router-link>
    </StateBlock>
  </main>
</template>
