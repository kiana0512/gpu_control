<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Bell,
  Box,
  Clock,
  Cpu,
  DataAnalysis,
  Document,
  Key,
  List,
  Operation,
  Setting,
  SwitchButton,
} from "@element-plus/icons-vue";
import { session } from "./api";

const route = useRoute();
const router = useRouter();
const menu = [
  ["/", "总览", Cpu],
  ["/jobs", "任务", List],
  ["/nodes", "GPU 节点", Box],
  ["/workflows", "工作流", DataAnalysis],
  ["/clients", "API 客户", Key],
  ["/scheduling", "调度策略", Operation],
  ["/alerts", "告警", Bell],
  ["/audit", "审计日志", Document],
  ["/logs", "日志中心", Clock],
  ["/settings", "系统设置", Setting],
] as const;
const showShell = computed(() => route.path != "/login");
function logout() {
  session.clear();
  router.push("/login");
}
</script>
<template>
  <router-view v-if="!showShell" />
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark"><i></i><b>GC</b></span>
        <strong>GPU Control<small>OPS</small></strong>
      </div>
      <nav aria-label="主导航">
        <router-link v-for="[path, label, icon] in menu" :key="path" :to="path"
          ><el-icon><component :is="icon" /></el-icon
          ><span>{{ label }}</span></router-link
        >
      </nav>
      <div class="cluster-state">
        <i></i><span>集群状态：正常</span><small>ComfyUI 集群 v1.1.0</small>
      </div>
    </aside>
    <section class="workspace">
      <header class="topbar">
        <span></span>
        <div>
          <button class="environment"><i></i>生产环境</button
          ><button class="user" @click="logout">
            <span class="avatar">管</span> administrator
            <el-icon><SwitchButton /></el-icon>
          </button>
        </div>
      </header>
      <main><router-view /></main>
    </section>
  </div>
</template>
