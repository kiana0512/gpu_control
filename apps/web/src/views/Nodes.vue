<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { NodeInfo } from "../types";
import StatusMark from "../components/StatusMark.vue";
const nodes = ref<NodeInfo[]>([]);
async function load() {
  nodes.value = await api.nodes();
}
async function mode(node: NodeInfo, value: NodeInfo["mode"]) {
  await ElMessageBox.confirm(
    `确认把 ${node.display_name} 切换为 ${value}？`,
    "节点操作二次确认",
    { type: "warning" },
  );
  await api.setMode(node.id, value, "管理员控制台操作");
  ElMessage.success("节点模式已更新");
  await load();
}
async function free(node: NodeInfo) {
  await ElMessageBox.confirm(
    `确认释放 ${node.display_name} 的模型显存？`,
    "释放模型",
    { type: "warning" },
  );
  await api.free(node.id);
  ElMessage.success("释放请求已执行");
}
async function operation(
  node: NodeInfo,
  action: "interrupt" | "restart" | "start" | "stop",
) {
  const labels = {
    interrupt: "中断",
    restart: "安全重启",
    start: "启动",
    stop: "停止",
  };
  await ElMessageBox.confirm(
    `确认对 ${node.display_name} 执行${labels[action]} ComfyUI？`,
    "破坏性操作二次确认",
    { type: "warning" },
  );
  await api[action](node.id);
  ElMessage.success("操作已执行");
  await load();
}
onMounted(load);
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>GPU 节点</h1>
        <p>Drain、Reserve、Release、中断、安全重启与模型显存管理</p>
      </div>
      <button class="secondary" @click="load">刷新</button>
    </div>
    <section class="node-detail blue-rail" v-for="node in nodes" :key="node.id">
      <div>
        <i :class="node.health.toLowerCase()"></i>
        <div>
          <h2>{{ node.display_name }}</h2>
          <span>{{ node.id }} · {{ node.pool }}</span>
        </div>
      </div>
      <StatusMark :value="node.mode" />
      <dl>
        <div>
          <dt>GPU 利用率</dt>
          <dd>{{ node.gpu_util_percent }}%</dd>
        </div>
        <div>
          <dt>可用显存</dt>
          <dd>{{ (node.free_vram_mb / 1024).toFixed(1) }} GB</dd>
        </div>
        <div>
          <dt>执行槽位</dt>
          <dd>{{ node.current_jobs }} / {{ node.max_concurrency }}</dd>
        </div>
      </dl>
      <div class="node-actions">
        <button @click="mode(node, 'DRAINING')">Drain</button
        ><button @click="mode(node, 'RESERVED')">Reserve</button
        ><button
          @click="mode(node, node.pool === 'PRIMARY' ? 'ACTIVE' : 'OVERFLOW')"
        >
          Release</button
        ><button @click="operation(node, 'start')">启动服务</button
        ><button @click="operation(node, 'stop')">停止服务</button
        ><button @click="operation(node, 'interrupt')">中断任务</button
        ><button @click="free(node)">释放模型</button
        ><button @click="operation(node, 'restart')">安全重启</button>
      </div>
    </section>
  </div>
</template>
