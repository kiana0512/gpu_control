<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api";
import type { AssetProcessingOverview, NodeInfo } from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";
import {
  codexRuntimeState,
  codexAdmissionLabel,
  codexAuthLabel,
  codexProbeLabel,
  healthyCodexProbeAverage,
  isCodexRuntimeNode,
} from "../codexPresentation";
import { compareNodes } from "../nodePresentation";

const nodes = ref<NodeInfo[]>([]);
const assets = ref<AssetProcessingOverview | null>(null);
const error = ref("");
const evaluatedAt = ref(Date.now());
const runtimeScope = ref<"all" | "attention" | "active">("all");
const selectedNodeId = ref<string | null>(null);
let freshnessTimer: number | undefined;
onMounted(() => {
  freshnessTimer = window.setInterval(() => {
    evaluatedAt.value = Date.now();
  }, 1000);
});
onBeforeUnmount(() => window.clearInterval(freshnessTimer));

const runtimes = computed(() =>
  nodes.value.filter(isCodexRuntimeNode).sort(compareNodes),
);
const runtimeState = (node: NodeInfo) =>
  codexRuntimeState(node, evaluatedAt.value);
const healthyCount = computed(
  () => runtimes.value.filter((node) => runtimeState(node).healthy).length,
);
const activeCount = computed(
  () => runtimes.value.filter((node) => node.codex_cli?.task?.is_active).length,
);
const authenticatedCount = computed(
  () =>
    runtimes.value.filter((node) => runtimeState(node).authenticated).length,
);
const averageLatency = computed(() =>
  healthyCodexProbeAverage(runtimes.value, evaluatedAt.value),
);
const attentionRuntimes = computed(() =>
  runtimes.value.filter((node) => !runtimeState(node).healthy),
);
const visibleRuntimes = computed(() => {
  if (runtimeScope.value === "attention") return attentionRuntimes.value;
  if (runtimeScope.value === "active")
    return runtimes.value.filter((node) => node.codex_cli?.task?.is_active);
  return runtimes.value;
});
const selectedNode = computed(
  () =>
    visibleRuntimes.value.find((node) => node.id === selectedNodeId.value) ??
    visibleRuntimes.value[0] ??
    null,
);
const clusterVerdict = computed(() => {
  if (!runtimes.value.length)
    return {
      tone: "unavailable",
      label: "尚未发现 Codex 运行时",
      detail: "等待节点上报或检查节点状态接口。",
    };
  if (attentionRuntimes.value.length)
    return {
      tone: "warning",
      label: `${attentionRuntimes.value.length} 个节点需要处理`,
      detail: "先处理认证、探针或心跳异常的运行时。",
    };
  return {
    tone: "healthy",
    label: "全部运行时可接单",
    detail: "每个已注册运行时均通过认证与真实调用探针。",
  };
});
watch(
  visibleRuntimes,
  (next) => {
    if (!next.some((node) => node.id === selectedNodeId.value)) {
      selectedNodeId.value = next[0]?.id ?? null;
    }
  },
  { immediate: true },
);

const workerNode = computed(() => {
  const result = new Map<string, string>();
  for (const worker of assets.value?.workers ?? [])
    result.set(worker.id, worker.node_id);
  return result;
});
const recentExecutions = computed(() =>
  (assets.value?.jobs ?? [])
    .filter((job) =>
      [
        "RETOPOLOGY_AUDIT",
        "RETOPOLOGY_PROCESS_V1",
        "RETOPOLOGY_PROCESS_V2",
      ].includes(job.job_type),
    )
    .slice(0, 8),
);

function time(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return "待上报";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
function taskTitle(node: NodeInfo) {
  const task = node.codex_cli?.task;
  if (!task) return "当前空闲";
  return task.is_active ? "正在执行资产任务" : "最近一次资产任务";
}
function statusLabel(status: string) {
  return (
    {
      PENDING: "排队中",
      RUNNING: "执行中",
      SUCCEEDED: "已完成",
      FAILED: "失败",
      CANCELLED: "已取消",
    }[status] ?? status
  );
}
async function load() {
  evaluatedAt.value = Date.now();
  const results = await Promise.allSettled([
    api.nodes(),
    api.assetProcessing(100),
  ]);
  if (results[0].status === "fulfilled") nodes.value = results[0].value;
  if (results[1].status === "fulfilled") assets.value = results[1].value;
  const failures = results.flatMap((result, index) =>
    result.status === "rejected"
      ? [
          (index === 0 ? "节点状态" : "任务历史") +
            "：" +
            (result.reason instanceof Error
              ? result.reason.message
              : "加载失败"),
        ]
      : [],
  );
  error.value = failures.join("；");
  if (failures.length) throw new Error(error.value);
}
const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page codex-workspace">
    <div class="page-heading workspace-heading">
      <div>
        <div class="workspace-kicker">RUNTIME CONTROL / CODEX</div>
        <h1>Codex 运行工作台</h1>
        <p>先处理不可接单节点，再按需下钻认证、探针与资产执行证据。</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state">
          <i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br />
          <small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          >
        </span>
        <button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>Codex 状态同步失败</strong><span>{{ error }}</span>
      <button @click="run">重试</button>
    </div>

    <section class="control-strip" aria-label="Codex 集群摘要">
      <div
        class="cluster-verdict"
        :class="clusterVerdict.tone"
        :title="clusterVerdict.detail"
      >
        <span class="verdict-dot"></span>
        <div>
          <small>运行结论</small>
          <strong>{{ clusterVerdict.label }}</strong>
        </div>
      </div>
      <dl class="inline-metrics">
        <div>
          <dt>健康</dt>
          <dd>{{ healthyCount }}/{{ runtimes.length }}</dd>
        </div>
        <div>
          <dt>认证</dt>
          <dd>{{ authenticatedCount }}</dd>
        </div>
        <div>
          <dt>执行中</dt>
          <dd>{{ activeCount }}</dd>
        </div>
        <div>
          <dt>探针均值</dt>
          <dd>{{ averageLatency == null ? "—" : averageLatency + " ms" }}</dd>
        </div>
      </dl>
    </section>

    <section v-if="attentionRuntimes.length" class="attention-band">
      <header>
        <span>需要处理</span
        ><strong>{{ attentionRuntimes.length }} 项运行时异常</strong>
      </header>
      <div class="attention-items">
        <button
          v-for="node in attentionRuntimes"
          :key="node.id"
          type="button"
          @click="selectedNodeId = node.id"
        >
          <span>{{ node.display_name }}</span
          ><b>{{ runtimeState(node).label }}</b>
          <small>{{ runtimeState(node).message }}</small
          ><i>查看证据 →</i>
        </button>
      </div>
    </section>

    <section class="runtime-console">
      <div class="runtime-index">
        <header class="section-bar">
          <div>
            <small>RUNTIME INDEX</small>
            <h2>节点运行时</h2>
          </div>
          <div class="scope-switch" aria-label="节点筛选">
            <button
              type="button"
              :class="{ active: runtimeScope === 'all' }"
              @click="runtimeScope = 'all'"
            >
              全部
            </button>
            <button
              type="button"
              :class="{ active: runtimeScope === 'attention' }"
              @click="runtimeScope = 'attention'"
            >
              待处理
            </button>
            <button
              type="button"
              :class="{ active: runtimeScope === 'active' }"
              @click="runtimeScope = 'active'"
            >
              执行中
            </button>
          </div>
        </header>
        <div class="runtime-columns" aria-hidden="true">
          <span>节点</span><span>状态</span><span>接单</span
          ><span>当前任务</span>
        </div>
        <button
          v-for="node in visibleRuntimes"
          :key="node.id"
          type="button"
          class="runtime-row"
          :class="[
            'tone-' + runtimeState(node).tone,
            { selected: selectedNode?.id === node.id },
          ]"
          @click="selectedNodeId = node.id"
        >
          <span class="runtime-identity"
            ><i>C</i><b>{{ node.display_name }}</b
            ><small>{{ node.id }}</small></span
          >
          <span class="state-cell"><i></i>{{ runtimeState(node).label }}</span>
          <span>{{ codexAdmissionLabel(node, evaluatedAt) }}</span>
          <span class="task-cell"
            ><b>{{ taskTitle(node) }}</b
            ><small>{{
              node.codex_cli?.task?.external_asset_id ?? "无任务"
            }}</small></span
          >
        </button>
        <p
          v-if="!visibleRuntimes.length && !refreshing"
          class="workspace-empty"
        >
          当前筛选范围没有节点
        </p>
      </div>

      <aside v-if="selectedNode" class="runtime-inspector">
        <header>
          <div>
            <small>SELECTED RUNTIME</small>
            <h2>{{ selectedNode.display_name }}</h2>
            <code>{{ selectedNode.id }}</code>
          </div>
          <span :class="'tone-' + runtimeState(selectedNode).tone">{{
            runtimeState(selectedNode).label
          }}</span>
        </header>
        <p class="inspector-message">
          {{ runtimeState(selectedNode).message }}
        </p>
        <div class="evidence-grid">
          <div>
            <span>主机 CLI</span
            ><strong>{{
              selectedNode.codex_cli?.host_version ?? "待上报"
            }}</strong>
          </div>
          <div>
            <span>Worker CLI</span
            ><strong>{{
              selectedNode.codex_cli?.runtime_version ?? "待上报"
            }}</strong>
          </div>
          <div>
            <span>认证</span
            ><strong>{{
              codexAuthLabel(selectedNode.codex_cli?.auth_status)
            }}</strong>
          </div>
          <div>
            <span>真实调用</span
            ><strong>{{
              codexProbeLabel(selectedNode.codex_cli?.probe_status)
            }}</strong>
          </div>
        </div>

        <section
          class="selected-task"
          :class="{ active: selectedNode.codex_cli?.task?.is_active }"
        >
          <small>{{ taskTitle(selectedNode) }}</small>
          <template v-if="selectedNode.codex_cli?.task">
            <h3>{{ selectedNode.codex_cli.task.external_asset_id }}</h3>
            <p>
              {{ selectedNode.codex_cli.task.stage }} ·
              {{ selectedNode.codex_cli.task.status }}
            </p>
            <dl>
              <div>
                <dt>输入</dt>
                <dd>{{ selectedNode.codex_cli.task.input.filename }}</dd>
              </div>
              <div>
                <dt>SHA</dt>
                <dd>{{ selectedNode.codex_cli.task.input.sha256 }}</dd>
              </div>
              <div>
                <dt>参考图</dt>
                <dd>
                  {{ selectedNode.codex_cli.task.input.reference_view_count }}
                  张
                </dd>
              </div>
              <div>
                <dt>高模</dt>
                <dd>
                  {{ selectedNode.codex_cli.task.input.high_object ?? "—" }}
                </dd>
              </div>
              <div>
                <dt>参考对象</dt>
                <dd>
                  {{
                    selectedNode.codex_cli.task.input.reference_object ?? "—"
                  }}
                </dd>
              </div>
              <div>
                <dt>低模</dt>
                <dd>
                  {{ selectedNode.codex_cli.task.input.low_object ?? "—" }}
                </dd>
              </div>
            </dl>
            <details>
              <summary>
                查看用户要求与
                {{ selectedNode.codex_cli.task.output_contract.length }}
                项输出合同
              </summary>
              <p>
                {{
                  selectedNode.codex_cli.task.input.user_request ??
                  "未提供额外要求"
                }}
              </p>
              <ul>
                <li
                  v-for="item in selectedNode.codex_cli.task.output_contract"
                  :key="item"
                >
                  {{ item }}
                </li>
              </ul>
            </details>
          </template>
          <template v-else
            ><h3>当前空闲</h3>
            <p>没有占用中的 Codex 任务。</p></template
          >
        </section>
        <details class="telemetry-details">
          <summary>时间与调度证据</summary>
          <dl>
            <div>
              <dt>Worker 心跳</dt>
              <dd>
                {{ time(selectedNode.codex_cli?.worker_last_heartbeat_at) }}
              </dd>
            </div>
            <div>
              <dt>最近探针</dt>
              <dd>{{ time(selectedNode.codex_cli?.last_checked_at) }}</dd>
            </div>
            <div>
              <dt>最近成功</dt>
              <dd>{{ time(selectedNode.codex_cli?.last_success_at) }}</dd>
            </div>
            <div>
              <dt>接单状态</dt>
              <dd>{{ codexAdmissionLabel(selectedNode, evaluatedAt) }}</dd>
            </div>
          </dl>
        </details>
      </aside>
      <aside v-else class="runtime-inspector empty-inspector">
        选择节点查看运行证据
      </aside>
    </section>

    <section class="execution-ledger">
      <header>
        <div>
          <small>EXECUTION LEDGER</small>
          <h2>最近 Codex 资产执行</h2>
          <p>
            输入、阶段、Worker 与终态可追溯；完整提示词和事件保留在任务详情。
          </p>
        </div>
        <span>{{ recentExecutions.length }} 条</span>
      </header>
      <div class="execution-table">
        <div class="execution-head">
          <span>任务 / 输入</span><span>Worker</span><span>阶段</span
          ><span>状态</span>
        </div>
        <div
          v-for="job in recentExecutions"
          :key="job.job_id"
          class="execution-row"
        >
          <div>
            <strong>{{ job.external_asset_id }}</strong
            ><small
              >{{ job.source_filename }} ·
              {{ job.input_sha256.slice(0, 12) }}…</small
            >
          </div>
          <span>{{
            job.worker_id
              ? (workerNode.get(job.worker_id) ?? job.worker_id)
              : "待分配"
          }}</span>
          <span>{{ job.stage_message || job.stage }}</span>
          <span class="execution-status" :class="job.status.toLowerCase()">{{
            statusLabel(job.status)
          }}</span>
        </div>
        <div v-if="!recentExecutions.length" class="workspace-empty">
          尚无 Codex 资产执行记录
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.codex-workspace {
  --p: #101a21;
  --r: #14222b;
  --l: rgba(137, 171, 184, 0.18);
  --m: #7f98a2;
  --t: #dbe8ec;
  --a: #61d7df;
  --g: #55d7a8;
  --w: #f0ba67;
  --b: #ff7c8c;
  display: grid;
  gap: 16px;
}
.workspace-heading {
  margin-bottom: 0;
}
.workspace-kicker,
.section-bar small,
.runtime-inspector header small,
.execution-ledger header small {
  color: var(--a);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.16em;
}
.control-strip {
  display: flex;
  min-height: 92px;
  border: 1px solid var(--l);
  border-radius: 10px;
  background:
    linear-gradient(110deg, rgba(85, 215, 168, 0.07), transparent 35%), var(--p);
  overflow: hidden;
}
.cluster-verdict {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 280px;
  padding: 18px 22px;
  border-right: 1px solid var(--l);
}
.verdict-dot {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--g);
  box-shadow: 0 0 0 5px rgba(85, 215, 168, 0.1);
}
.warning .verdict-dot {
  background: var(--w);
  box-shadow: 0 0 0 5px rgba(240, 186, 103, 0.1);
}
.unavailable .verdict-dot {
  background: var(--b);
  box-shadow: 0 0 0 5px rgba(255, 124, 140, 0.1);
}
.cluster-verdict div {
  display: grid;
  gap: 5px;
}
.cluster-verdict small,
.inline-metrics dt {
  color: var(--m);
  font-size: 11px;
}
.cluster-verdict strong {
  color: var(--t);
  font-size: 16px;
}
.inline-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(110px, 1fr));
  flex: 1;
  margin: 0;
}
.inline-metrics div {
  display: grid;
  align-content: center;
  gap: 5px;
  padding: 0 22px;
  border-right: 1px solid var(--l);
}
.inline-metrics dd {
  margin: 0;
  color: #f4fbfc;
  font-size: 20px;
  font-weight: 750;
}
.attention-band {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  border: 1px solid rgba(240, 186, 103, 0.28);
  border-radius: 10px;
  background: rgba(240, 186, 103, 0.035);
  overflow: hidden;
}
.attention-band header,
.attention-band button {
  padding: 14px 16px;
  border: 0;
  background: transparent;
  text-align: left;
}
.attention-band header {
  display: grid;
  align-content: center;
  gap: 5px;
  border-right: 1px solid var(--l);
}
.attention-items {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  min-width: 0;
}
.attention-items button {
  border-right: 1px solid var(--l);
}
.attention-items button:last-child {
  border-right: 0;
}
.attention-band header span,
.attention-band button b {
  color: var(--w);
  font-size: 11px;
}
.attention-band header strong,
.attention-band button span {
  color: var(--t);
}
.attention-band button {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 5px 12px;
  cursor: pointer;
}
.attention-band button small,
.attention-band button i {
  grid-column: 1/-1;
  color: var(--m);
  font-size: 11px;
}
.attention-band button i {
  color: var(--a);
  font-style: normal;
}
.runtime-console {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(330px, 0.8fr);
  min-height: 560px;
  border: 1px solid var(--l);
  border-radius: 10px;
  background: var(--p);
  overflow: hidden;
}
.runtime-index {
  min-width: 0;
  border-right: 1px solid var(--l);
}
.section-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 70px;
  padding: 0 18px;
  border-bottom: 1px solid var(--l);
}
.section-bar h2,
.runtime-inspector h2,
.execution-ledger h2 {
  margin: 4px 0 0;
  color: var(--t);
  font-size: 16px;
}
.scope-switch {
  display: flex;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--l);
  border-radius: 7px;
}
.scope-switch button {
  padding: 6px 10px;
  border: 0;
  border-radius: 5px;
  color: var(--m);
  background: transparent;
  cursor: pointer;
}
.scope-switch button.active {
  color: #e9fbfc;
  background: rgba(97, 215, 223, 0.14);
}
.runtime-columns,
.runtime-row {
  display: grid;
  grid-template-columns:
    minmax(190px, 1.2fr) minmax(100px, 0.65fr) minmax(120px, 0.8fr)
    minmax(170px, 1fr);
  align-items: center;
  gap: 14px;
  padding: 0 18px;
}
.runtime-columns {
  min-height: 34px;
  color: #68818b;
  background: #0c161c;
  font-size: 10px;
}
.runtime-row {
  width: 100%;
  min-height: 76px;
  border: 0;
  border-top: 1px solid var(--l);
  color: #a9bec5;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.runtime-row:hover,
.runtime-row.selected {
  background: rgba(97, 215, 223, 0.045);
}
.runtime-row.selected {
  box-shadow: inset 2px 0 var(--a);
}
.runtime-identity {
  display: grid;
  grid-template-columns: 32px 1fr;
  min-width: 0;
}
.runtime-identity i {
  grid-row: 1/3;
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid rgba(97, 215, 223, 0.35);
  border-radius: 6px;
  color: var(--a);
  font-style: normal;
  font-weight: 800;
}
.runtime-identity b,
.task-cell b {
  overflow: hidden;
  color: var(--t);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runtime-identity small,
.task-cell small {
  overflow: hidden;
  color: #6f8892;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.state-cell {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11px;
}
.state-cell i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #70838a;
}
.tone-healthy .state-cell i {
  background: var(--g);
}
.tone-stale .state-cell i,
.tone-checking .state-cell i {
  background: var(--w);
}
.tone-degraded .state-cell i,
.tone-unavailable .state-cell i {
  background: var(--b);
}
.task-cell {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.runtime-inspector {
  min-width: 0;
  padding: 20px;
  background: #0c171d;
}
.runtime-inspector > header {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--l);
}
.runtime-inspector header code {
  color: #718993;
  font-size: 11px;
}
.runtime-inspector header > span {
  padding: 5px 8px;
  border-radius: 999px;
  color: var(--g);
  background: rgba(85, 215, 168, 0.1);
  font-size: 10px;
}
.runtime-inspector header > .tone-degraded,
.runtime-inspector header > .tone-unavailable {
  color: var(--b);
}
.runtime-inspector header > .tone-stale,
.runtime-inspector header > .tone-checking {
  color: var(--w);
}
.inspector-message {
  min-height: 42px;
  margin: 15px 0;
  color: #9bb0b8;
  font-size: 12px;
  line-height: 1.6;
}
.evidence-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border: 1px solid var(--l);
  border-radius: 8px;
  overflow: hidden;
}
.evidence-grid div {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 10px 12px;
  border-right: 1px solid var(--l);
  border-bottom: 1px solid var(--l);
}
.evidence-grid span,
.selected-task small {
  color: var(--m);
  font-size: 10px;
}
.evidence-grid strong {
  overflow-wrap: anywhere;
  color: var(--t);
  font-size: 11px;
}
.selected-task {
  margin-top: 14px;
  padding: 14px;
  border-left: 2px solid #657a83;
  background: rgba(255, 255, 255, 0.018);
}
.selected-task.active {
  border-left-color: var(--g);
}
.selected-task h3 {
  margin: 6px 0 4px;
  overflow-wrap: anywhere;
  color: var(--t);
  font-size: 13px;
}
.selected-task > p {
  margin: 0 0 12px;
  color: var(--m);
  font-size: 11px;
}
.selected-task dl,
.telemetry-details dl {
  display: grid;
  gap: 7px;
  margin: 0;
}
.selected-task dl div,
.telemetry-details dl div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 8px;
}
.selected-task dt,
.selected-task dd,
.telemetry-details dt,
.telemetry-details dd {
  margin: 0;
  font-size: 10px;
}
.selected-task dt,
.telemetry-details dt {
  color: #6f8790;
}
.selected-task dd,
.telemetry-details dd {
  overflow-wrap: anywhere;
  color: #b8cbd1;
}
.selected-task details,
.telemetry-details {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--l);
  color: #a8bcc3;
  font-size: 11px;
}
summary {
  color: var(--a);
  cursor: pointer;
}
.empty-inspector {
  display: grid;
  place-items: center;
  color: var(--m);
}
.execution-ledger {
  border: 1px solid var(--l);
  border-radius: 10px;
  background: var(--p);
  overflow: hidden;
}
.execution-ledger > header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 76px;
  padding: 0 20px;
  border-bottom: 1px solid var(--l);
}
.execution-ledger header p {
  margin: 4px 0 0;
  color: var(--m);
  font-size: 11px;
}
.execution-ledger > header > span {
  color: var(--a);
  font-size: 11px;
}
.execution-head,
.execution-row {
  display: grid;
  grid-template-columns:
    minmax(260px, 1.5fr) minmax(140px, 0.7fr) minmax(200px, 1fr)
    90px;
  align-items: center;
  gap: 16px;
  padding: 0 20px;
}
.execution-head {
  min-height: 34px;
  color: #68818b;
  background: #0c161c;
  font-size: 10px;
}
.execution-row {
  min-height: 66px;
  border-top: 1px solid var(--l);
  color: #a9bec5;
  font-size: 11px;
}
.execution-row strong,
.execution-row small {
  display: block;
  overflow-wrap: anywhere;
}
.execution-row strong {
  color: var(--t);
  font-size: 12px;
}
.execution-row small {
  color: #6f8790;
}
.execution-status {
  padding: 4px 7px;
  border-radius: 5px;
  background: var(--r);
}
.execution-status.succeeded {
  color: var(--g);
}
.execution-status.failed,
.execution-status.cancelled {
  color: var(--b);
}
.workspace-empty {
  margin: 0;
  padding: 30px;
  color: var(--m);
  text-align: center;
}
@media (max-width: 1120px) {
  .runtime-console {
    grid-template-columns: 1fr;
  }
  .runtime-index {
    border-right: 0;
  }
  .attention-band {
    grid-template-columns: 160px minmax(0, 1fr);
  }
}
@media (max-width: 800px) {
  .control-strip {
    display: grid;
  }
  .inline-metrics {
    grid-template-columns: 1fr 1fr;
  }
  .attention-band {
    grid-template-columns: 1fr;
  }
  .attention-band header {
    border-right: 0;
    border-bottom: 1px solid var(--l);
  }
  .runtime-columns {
    display: none;
  }
  .runtime-row {
    grid-template-columns: 1fr 1fr;
    padding: 12px 16px;
  }
  .execution-head {
    display: none;
  }
  .execution-row {
    grid-template-columns: 1fr;
    padding: 14px 18px;
  }
}
@media (max-width: 560px) {
  .section-bar {
    align-items: flex-start;
    flex-direction: column;
    padding: 14px;
  }
  .runtime-row,
  .evidence-grid {
    grid-template-columns: 1fr;
  }
}
</style>
