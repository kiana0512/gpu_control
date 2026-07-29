<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { NodeInfo } from "../types";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const nodes = ref<NodeInfo[]>([]);
const error = ref("");
const maintenanceNode = ref<NodeInfo | null>(null);
const connectedNodes = computed(() =>
  nodes.value.filter(
    (node) => node.last_heartbeat_at || node.health !== "OFFLINE",
  ),
);
const onlineCount = computed(
  () => connectedNodes.value.filter((node) => node.health === "ONLINE").length,
);
async function load() {
  error.value = "";
  try {
    nodes.value = await api.nodes();
    if (maintenanceNode.value) {
      maintenanceNode.value =
        nodes.value.find((node) => node.id === maintenanceNode.value?.id) ??
        null;
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "节点数据加载失败";
    throw cause;
  }
}

async function mode(node: NodeInfo, value: NodeInfo["mode"]) {
  const labels: Record<NodeInfo["mode"], string> = {
    ACTIVE: "投入使用",
    RESERVED: "暂停接单",
    OVERFLOW: "设为备用算力",
    DRAINING: "排空任务",
    DISABLED: "禁用",
  };
  await ElMessageBox.confirm(
    `确认对 ${node.display_name} 执行“${labels[value]}”吗？`,
    "确认节点状态变更",
    { type: value === "ACTIVE" ? "info" : "warning" },
  );
  await api.setMode(node.id, value, `管理员执行：${labels[value]}`);
  ElMessage.success(`${node.display_name} 已${labels[value]}`);
  await load();
}

async function free(node: NodeInfo) {
  await ElMessageBox.confirm(
    `确认释放 ${node.display_name} 的模型显存吗？空闲模型会被卸载。`,
    "释放模型显存",
    { type: "warning" },
  );
  await api.free(node.id);
  ElMessage.success("显存释放请求已执行");
  await load();
}

async function operation(
  node: NodeInfo,
  action: "interrupt" | "restart" | "start" | "stop",
) {
  const labels = {
    interrupt: "中断当前任务",
    restart: "安全重启 ComfyUI",
    start: "启动 ComfyUI",
    stop: "停止 ComfyUI",
  };
  await ElMessageBox.confirm(
    `确认对 ${node.display_name} 执行“${labels[action]}”吗？`,
    "确认维护操作",
    { type: action === "start" ? "info" : "warning" },
  );
  await api[action](node.id);
  ElMessage.success(`${labels[action]}请求已执行`);
  await load();
}

function openComfy(node: NodeInfo) {
  if (!node.base_url) {
    ElMessage.warning("该节点尚未上报 ComfyUI 地址");
    return;
  }
  const url = new URL(node.base_url);
  if (node.id === "control-4090") {
    url.hostname = window.location.hostname;
    url.hash = "551d82b0-b1fb-483a-a5ea-564bdb813625";
  }
  window.open(url.toString(), "_blank", "noopener,noreferrer");
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page nodes-page">
    <div class="page-heading">
      <div>
        <div class="eyebrow">统一计算节点</div>
        <h1>GPU 推理节点</h1>
        <p>{{ onlineCount }} 台 GPU 在线 · ComfyUI 与推理槽独立管理</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br /><small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          ></span
        >
        <button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>节点同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <div class="node-list">
      <section
        v-for="node in connectedNodes"
        :key="node.id"
        class="node-card"
        :class="{ offline: node.health !== 'ONLINE' }"
      >
        <div class="node-main-row">
          <div class="node-identity">
            <span class="health-dot" :class="node.health.toLowerCase()"></span>
            <div>
              <h2>{{ node.display_name }}</h2>
              <p>
                {{ node.id }} ·
                {{ node.pool === "PRIMARY" ? "主算力" : "备用算力" }}
              </p>
            </div>
          </div>

          <StatusMark :value="node.mode" />

          <div class="node-metrics">
            <div>
              <span>GPU 利用率</span
              ><strong>{{
                node.health === "ONLINE" ? `${node.gpu_util_percent}%` : "—"
              }}</strong>
            </div>
            <div>
              <span>可用显存</span
              ><strong>{{
                node.health === "ONLINE"
                  ? `${(node.free_vram_mb / 1024).toFixed(1)} GB`
                  : "—"
              }}</strong>
            </div>
            <div>
              <span>执行槽位</span
              ><strong>{{
                node.health === "ONLINE"
                  ? `${node.current_jobs} / ${node.max_concurrency}`
                  : "—"
              }}</strong>
            </div>
          </div>

          <div v-if="node.health === 'ONLINE'" class="node-primary-actions">
            <button
              v-if="node.mode !== 'ACTIVE'"
              class="primary"
              @click="mode(node, 'ACTIVE')"
            >
              投入使用
            </button>
            <button v-else class="pause-button" @click="mode(node, 'RESERVED')">
              暂停接单
            </button>
            <button class="secondary" @click="openComfy(node)">
              打开 ComfyUI
            </button>
            <button class="secondary" @click="maintenanceNode = node">
              维护操作
            </button>
          </div>
          <div v-else class="offline-node-note">
            设备心跳离线，恢复上报后可操作
          </div>
        </div>
      </section>
      <div
        v-if="!connectedNodes.length && !refreshing"
        class="empty-state action-empty"
      >
        <strong>尚无 GPU 节点接入</strong
        ><span>节点首次上报真实心跳后会自动显示在这里。</span>
      </div>
    </div>

    <div
      v-if="maintenanceNode"
      class="panel-backdrop maintenance-backdrop"
      @click.self="maintenanceNode = null"
    >
      <aside
        class="maintenance-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="节点维护操作"
      >
        <header>
          <div>
            <h2>维护操作</h2>
            <p>{{ maintenanceNode.display_name }} · {{ maintenanceNode.id }}</p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            @click="maintenanceNode = null"
          >
            ×
          </button>
        </header>

        <div class="drawer-content">
          <section>
            <h3>调度状态</h3>
            <p>控制调度器是否继续向这张 GPU 分配新任务。</p>
            <button
              v-if="maintenanceNode.pool === 'OVERFLOW'"
              class="drawer-action"
              @click="mode(maintenanceNode, 'OVERFLOW')"
            >
              <strong>设为备用算力</strong
              ><span>仅在开启自动溢出并达到阈值时接单</span>
            </button>
            <button
              class="drawer-action"
              @click="mode(maintenanceNode, 'DRAINING')"
            >
              <strong>排空任务</strong
              ><span>停止接收新任务，等待当前任务自然结束</span>
            </button>
          </section>

          <section>
            <h3>ComfyUI 服务</h3>
            <p>管理节点上的 ComfyUI Docker 服务。</p>
            <button
              class="drawer-action"
              @click="operation(maintenanceNode, 'start')"
            >
              <strong>启动 ComfyUI</strong><span>服务停止时重新启动容器</span>
            </button>
            <button
              class="drawer-action warning"
              @click="operation(maintenanceNode, 'stop')"
            >
              <strong>停止 ComfyUI</strong
              ><span>停止服务，未完成任务会失败</span>
            </button>
            <button
              class="drawer-action"
              @click="operation(maintenanceNode, 'restart')"
            >
              <strong>安全重启</strong><span>节点空闲后重新启动 ComfyUI</span>
            </button>
          </section>

          <section class="danger-zone">
            <h3>故障处理</h3>
            <p>仅在任务卡住或显存异常时使用。</p>
            <button
              class="drawer-action warning"
              @click="operation(maintenanceNode, 'interrupt')"
            >
              <strong>中断当前任务</strong
              ><span>清空正在执行的 ComfyUI 任务</span>
            </button>
            <button class="drawer-action" @click="free(maintenanceNode)">
              <strong>释放模型显存</strong
              ><span>卸载空闲模型并清理显存缓存</span>
            </button>
          </section>
        </div>
        <footer>
          <button class="secondary" @click="maintenanceNode = null">
            关闭
          </button>
        </footer>
      </aside>
    </div>
  </div>
</template>
