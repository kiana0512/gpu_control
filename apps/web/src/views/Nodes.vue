<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { NodeInfo } from "../types";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";
import { formatGpuPower, formatGpuTemperature } from "../nodePresentation";

const nodes = ref<NodeInfo[]>([]);
const error = ref("");
const maintenanceNode = ref<NodeInfo | null>(null);
const onlineCount = computed(
  () => nodes.value.filter((node) => node.health !== "OFFLINE").length,
);

function metricAvailable(node: NodeInfo) {
  return node.health !== "OFFLINE";
}

type Specialization = {
  key: string;
  expiresAt: Date;
  remainingMinutes: number;
};

function specialization(node: NodeInfo): Specialization | null {
  const raw = node.labels?.gpu_specialization;
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.key !== "string" || typeof value.expires_at !== "string")
    return null;
  const expiresAt = new Date(value.expires_at);
  const remainingMs = expiresAt.getTime() - Date.now();
  if (!Number.isFinite(expiresAt.getTime()) || remainingMs <= 0) return null;
  return {
    key: value.key,
    expiresAt,
    remainingMinutes: Math.max(1, Math.ceil(remainingMs / 60_000)),
  };
}

function automaticRecoveryTime(active: Specialization) {
  return active.expiresAt.toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function hasSubstanceOwnedDrain(node: NodeInfo) {
  return node.labels?.substance_bake_drain_owner === "asset-api";
}

function hasLiveSubstanceInterlock(node: NodeInfo) {
  const labels = node.labels ?? {};
  const fences = labels.substance_bake_fence_job_ids;
  const recovery = labels.substance_bake_recovery_required;
  const pending = labels.substance_bake_pending_reservation;
  const pendingValue =
    pending && typeof pending === "object"
      ? (pending as Record<string, unknown>)
      : null;
  const pendingExpiry =
    typeof pendingValue?.expires_at === "string"
      ? new Date(pendingValue.expires_at).getTime()
      : 0;
  return Boolean(
    labels.substance_bake_fence_job_id ||
      (Array.isArray(fences) && fences.length) ||
      (Array.isArray(recovery) && recovery.length) ||
      (Array.isArray(pendingValue?.job_ids) &&
        pendingValue.job_ids.length &&
        pendingExpiry > Date.now()),
  );
}

function nodePolicyTitle(node: NodeInfo) {
  const active = specialization(node);
  if (node.id === "worker-4070ti-animation-host-01") {
    if (active?.key === "modelview-inpaint")
      return `局部重绘保护中 · 约 ${active.remainingMinutes} 分钟`;
    return "局部重绘优先 · 当前为四卡共享状态";
  }
  if (node.id === "worker-3090-b") {
    if (active?.key === "substance-bake")
      return `烘焙保护中 · 预计 ${automaticRecoveryTime(active)} 自动恢复`;
    if (
      node.mode === "DRAINING" &&
      hasSubstanceOwnedDrain(node) &&
      !hasLiveSubstanceInterlock(node)
    )
      return "烘焙保护回收中 · 下一次健康心跳自动恢复";
    return "唯一 Substance 烘焙节点 · 当前为普通 GPU 状态";
  }
  return "普通共享 GPU 节点";
}

function nodePolicyDetail(node: NodeInfo) {
  const active = specialization(node);
  if (node.id === "worker-4070ti-animation-host-01") {
    if (active?.key === "modelview-inpaint")
      return node.current_jobs
        ? "局部重绘已取得优先权；冲突的抠图帧会安全中断并改派其它物理 GPU。"
        : "只领取局部重绘；每次新任务刷新窗口，硬过期后自动恢复抠图与粗糙度。";
    return "可接抠图、局部重绘、粗糙度；不具备 Substance 烘焙能力。";
  }
  if (node.id === "worker-3090-b") {
    if (active?.key === "substance-bake")
      return node.current_jobs
        ? "停止领取新抠图，等待当前帧自然完成后清显存并切换 Windows Baker。"
        : "GPU 保留给生产烘焙；5 分钟无新烘焙后自动恢复，空闲时也可由管理员立即解除。";
    if (
      node.mode === "DRAINING" &&
      hasSubstanceOwnedDrain(node) &&
      !hasLiveSubstanceInterlock(node)
    )
      return "软保护已到期，Asset API 正在清理过期标签并回写 ACTIVE；无需人工等待。";
    return "可接普通推理；生产烘焙到达后获得下一 GPU 执行权。";
  }
  return "按兼容性、缓存亲和与公平队列参与抠图、局部重绘和粗糙度。";
}
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

async function releaseSubstanceProtection(node: NodeInfo) {
  await ElMessageBox.confirm(
    `确认立即解除 ${node.display_name} 的空闲烘焙保护并恢复普通 GPU 接单吗？活动 Baker、待领取烘焙或恢复门禁存在时后端仍会拒绝解除。`,
    "解除空闲烘焙保护",
    { type: "warning" },
  );
  await api.setMode(
    node.id,
    "ACTIVE",
    "管理员确认当前无活动烘焙，立即解除空闲烘焙保护",
  );
  ElMessage.success("3090-B 已恢复普通 GPU 接单");
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

    <section class="gpu-specialization-guide">
      <div>
        <strong>四卡共享，不是单机绑定</strong>
        <span
          >空闲时，抠图与局部重绘都可分配到
          4090、3090-A、3090-B、4070Ti；粗糙度按兼容性使用空闲 GPU。</span
        >
      </div>
      <div>
        <strong>4070Ti 保证局部重绘响应</strong>
        <span
          >与抠图冲突时让出抠图帧并改派其它 GPU；进入可续期的 15
          分钟局部重绘保护。</span
        >
      </div>
      <div>
        <strong>3090-B 保证唯一烘焙通道</strong>
        <span
          >烘焙排队后不再接新抠图，当前帧自然结束再切换；5
          分钟无新任务会硬过期。</span
        >
      </div>
      <p>
        GPU 保护只影响 GPU 单槽；各节点 CPU 拓扑、拆 UV 等 Asset Worker
        槽位不受影响。
      </p>
    </section>

    <div class="node-list">
      <section
        v-for="node in nodes"
        :key="node.id"
        class="node-card"
        :class="{
          offline: node.health === 'OFFLINE',
          degraded: node.health === 'DEGRADED',
        }"
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
                metricAvailable(node) ? `${node.gpu_util_percent}%` : "—"
              }}</strong>
            </div>
            <div>
              <span>温度</span><strong>{{ formatGpuTemperature(node) }}</strong>
            </div>
            <div>
              <span>功率</span><strong>{{ formatGpuPower(node) }}</strong>
            </div>
            <div>
              <span>可用显存</span
              ><strong>{{
                metricAvailable(node)
                  ? `${(node.free_vram_mb / 1024).toFixed(1)} GB`
                  : "—"
              }}</strong>
            </div>
            <div>
              <span>执行槽位</span
              ><strong>{{
                metricAvailable(node)
                  ? `${node.current_jobs} / ${node.max_concurrency}`
                  : "—"
              }}</strong>
            </div>
          </div>

          <div v-if="node.health === 'ONLINE'" class="node-primary-actions">
            <button
              v-if="
                node.id === 'worker-3090-b' &&
                node.mode === 'ACTIVE' &&
                node.current_jobs === 0 &&
                specialization(node)?.key === 'substance-bake' &&
                !hasLiveSubstanceInterlock(node)
              "
              class="primary"
              @click="releaseSubstanceProtection(node)"
            >
              解除烘焙保护
            </button>
            <button
              v-else-if="node.mode !== 'ACTIVE'"
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
          <div
            v-else
            class="offline-node-note"
            :class="{ degraded: node.health === 'DEGRADED' }"
          >
            {{
              node.health === "DEGRADED"
                ? "Node Agent 在线 · ComfyUI 忙碌，指标由独立探针持续上报"
                : "设备与 Node Agent 心跳离线，恢复上报后可操作"
            }}
          </div>
        </div>
        <div
          class="node-policy-strip"
          :class="{
            specialist: specialization(node),
            inpaint: node.id === 'worker-4070ti-animation-host-01',
            bake: node.id === 'worker-3090-b',
          }"
        >
          <strong>{{ nodePolicyTitle(node) }}</strong>
          <span>{{ nodePolicyDetail(node) }}</span>
          <small>GPU 1 槽 · CPU Asset 独立</small>
        </div>
      </section>
      <div v-if="!nodes.length && !refreshing" class="empty-state action-empty">
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
