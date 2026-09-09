<script setup lang="ts">
// Shell: sidebar navigation + routed content.
</script>

<template>
  <el-container class="shell">
    <el-aside width="220px" class="aside">
      <div class="brand">medops 运维平台</div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">总览 Dashboard</el-menu-item>
        <el-menu-item index="/devices">设备台账</el-menu-item>
        <el-menu-item index="/alerts">告警中心</el-menu-item>
        <el-menu-item index="/work-orders">工单管理</el-menu-item>
        <el-menu-item index="/maintenance">维保管理</el-menu-item>
        <el-menu-item index="/mcp-servers">MCP 服务</el-menu-item>
        <el-menu-item index="/api-endpoints">API 接入</el-menu-item>
        <el-menu-item index="/chat">智能问答</el-menu-item>
        <el-menu-item index="/knowledge">知识库</el-menu-item>
        <el-menu-item index="/plugins">插件</el-menu-item>
        <el-menu-item index="/cleanup">数据清理</el-menu-item>
        <el-menu-item index="/help">使用说明</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<style>
html, body, #app { height: 100%; margin: 0; }
.shell { height: 100%; }
.aside { border-right: 1px solid #e4e7ed; }
.brand { font-weight: 600; padding: 18px 20px; font-size: 16px; }
.menu { border-right: none; }
</style>
