<script setup lang="ts">
import type { NodeInfo } from "../types";
import StatusMark from "./StatusMark.vue";
defineProps<{ nodes: NodeInfo[] }>();
</script>
<template>
  <section class="ruled-section blue-rail">
    <div class="section-title">
      <h2>GPU 节点</h2>
      <router-link to="/nodes">管理节点 →</router-link>
    </div>
    <div class="node-head">
      <span>节点名称</span><span>状态</span><span>GPU 利用率</span
      ><span>显存 (VRAM)</span><span>运行任务</span><span>池</span>
    </div>
    <router-link
      class="node-row"
      v-for="node in nodes"
      :key="node.id"
      to="/nodes"
      ><strong
        ><i :class="node.health.toLowerCase()"></i
        >{{ node.display_name }}</strong
      ><StatusMark :value="node.mode" /><span
        >{{ node.gpu_util_percent.toFixed(0) }}%<b
          ><em :style="{ width: `${node.gpu_util_percent}%` }"></em></b></span
      ><span
        >{{ (node.free_vram_mb / 1024).toFixed(1) }} /
        {{ (node.total_vram_mb / 1024).toFixed(0) }} GB</span
      ><span>{{ node.current_jobs }} / {{ node.max_concurrency }}</span
      ><span>{{ node.pool }}</span></router-link
    >
  </section>
</template>
