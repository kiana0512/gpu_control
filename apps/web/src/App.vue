<script setup lang="ts">
import { computed, type Component } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  Bell,
  Box,
  Clock,
  Connection,
  Cpu,
  DataAnalysis,
  Document,
  Key,
  List,
  Operation,
  SetUp,
  Setting,
  SwitchButton,
  TrendCharts,
} from "@element-plus/icons-vue";
import { session } from "./api";

const route = useRoute();
const router = useRouter();
const buildVersion = import.meta.env.VITE_GPU_CONTROL_VERSION || "development";
const buildRevision = (
  import.meta.env.VITE_GPU_CONTROL_REVISION || "unknown"
).slice(0, 12);
type MenuItem = { path: string; label: string; icon: Component };
type MenuGroup = { label: string; items: MenuItem[] };
const menuGroups: MenuGroup[] = [
  {
    label: "运行工作台",
    items: [
      { path: "/", label: "总览", icon: Cpu },
      { path: "/jobs", label: "任务中心", icon: List },
      { path: "/analysis", label: "性能分析", icon: TrendCharts },
    ],
  },
  {
    label: "计算能力",
    items: [
      { path: "/nodes", label: "GPU 节点", icon: Box },
      { path: "/asset-processing", label: "资产处理", icon: SetUp },
      { path: "/codex", label: "Codex Workers", icon: Connection },
    ],
  },
  {
    label: "接入与治理",
    items: [
      { path: "/workflows", label: "工作流", icon: DataAnalysis },
      { path: "/clients", label: "API 客户", icon: Key },
      { path: "/scheduling", label: "调度说明", icon: Operation },
    ],
  },
  {
    label: "诊断与系统",
    items: [
      { path: "/alerts", label: "告警", icon: Bell },
      { path: "/audit", label: "审计", icon: Document },
      { path: "/logs", label: "日志", icon: Clock },
      { path: "/settings", label: "系统信息", icon: Setting },
    ],
  },
];
const menu = menuGroups.flatMap((group) => group.items);
const showShell = computed(() => route.path != "/login");
const currentSection = computed(
  () => menu.find((item) => item.path === route.path)?.label ?? "控制台",
);
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
        <strong><span>GPU Control</span><small>统一调度中心</small></strong>
      </div>
      <nav aria-label="主导航">
        <section
          v-for="group in menuGroups"
          :key="group.label"
          class="nav-group"
        >
          <span class="nav-group-label">{{ group.label }}</span>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </router-link>
        </section>
      </nav>
      <div class="cluster-state">
        <i></i><span>控制平面在线</span
        ><small
          >Scheduler {{ buildVersion }}<br />Revision {{ buildRevision }}</small
        >
      </div>
    </aside>
    <section class="workspace">
      <header class="topbar">
        <div class="breadcrumb">
          <span>GPU Control</span><b>/</b><strong>{{ currentSection }}</strong>
        </div>
        <div>
          <button type="button" class="environment"><i></i>生产环境</button
          ><button type="button" class="user" @click="logout">
            <span class="avatar">管</span> administrator
            <el-icon><SwitchButton /></el-icon>
          </button>
        </div>
      </header>
      <main><router-view /></main>
    </section>
  </div>
</template>
