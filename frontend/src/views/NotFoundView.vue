<script setup lang="ts">
import StateBlock from "../components/ui/StateBlock.vue"
</script>

<template>
  <main class="page">
    <StateBlock
      state="empty"
      title="页面不存在"
      message="这个地址还没有对应功能。可以从左侧导航回到研究驾驶舱。"
    >
      <router-link class="text-button" to="/dashboard">返回研究驾驶舱</router-link>
    </StateBlock>
  </main>
</template>
