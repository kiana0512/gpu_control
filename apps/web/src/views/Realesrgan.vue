<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { useAutoRefresh } from "../composables/useAutoRefresh";
import { compareNodes } from "../nodePresentation";

type Status = Awaited<ReturnType<typeof api.realesrgan>>;
const status = ref<Status | null>(null);
const error = ref("");
const selectedNodeId = ref<string | null>(null);
const taskScope = ref<"all" | "active" | "failed">("all");
const origin = window.location.origin;

const orderedNodes = computed(() =>
  [...(status.value?.nodes ?? [])].sort(compareNodes),
);
const readyNodes = computed(() =>
  orderedNodes.value.filter((node) => node.ready),
);
const issueNodes = computed(() =>
  orderedNodes.value.filter((node) => !node.ready || node.last_error),
);
const allReady = computed(
  () =>
    orderedNodes.value.length > 0 &&
    readyNodes.value.length === orderedNodes.value.length,
);
const activeSlots = computed(() =>
  orderedNodes.value.reduce((sum, node) => sum + node.active, 0),
);
const activeTasks = computed(
  () =>
    status.value?.tasks.filter((task) =>
      ["QUEUED", "RUNNING"].includes(task.status),
    ).length ?? 0,
);
const failedTasks = computed(
  () =>
    status.value?.tasks.filter((task) => task.status === "FAILED").length ?? 0,
);
const visibleTasks = computed(() => {
  const tasks = status.value?.tasks ?? [];
  if (taskScope.value === "active")
    return tasks.filter((task) => ["QUEUED", "RUNNING"].includes(task.status));
  if (taskScope.value === "failed")
    return tasks.filter((task) => task.status === "FAILED");
  return tasks;
});
const selectedNode = computed(
  () =>
    orderedNodes.value.find((node) => node.id === selectedNodeId.value) ??
    orderedNodes.value[0] ??
    null,
);
watch(
  orderedNodes,
  (nodes) => {
    if (!nodes.some((node) => node.id === selectedNodeId.value)) {
      selectedNodeId.value =
        nodes.find((node) => !node.ready || node.last_error)?.id ??
        nodes[0]?.id ??
        null;
    }
  },
  { immediate: true },
);

const enhanceUrl = computed(
  () =>
    origin +
    (status.value?.api.enhance ?? "/api/v1/realesrgan/enhance?strength=0.7"),
);
const readyUrl = computed(
  () => origin + (status.value?.api.ready ?? "/api/v1/realesrgan/ready"),
);
const curlExample = computed(() =>
  [
    "INPUT=rgba-input.png",
    "INPUT_SHA=$(sha256sum \"$INPUT\" | awk '{print $1}')",
    "IDEM=$(printf 'RealESRGAN_x4plus_anime_6B\\0%s\\0%s' '0.7' \"$INPUT_SHA\" | sha256sum | awk '{print $1}')",
    "",
    "curl --fail-with-body \\",
    "  --cacert GPU_CONTROL_LAN_CA.crt \\",
    "  -X POST '" + enhanceUrl.value + "' \\",
    '  -H "X-API-Key: $' + '{GPU_CONTROL_API_KEY}" \\',
    '  -H "X-Request-ID: liclip-esrgan-001" \\',
    '  -H "Idempotency-Key: $IDEM" \\',
    '  -H "X-Input-SHA256: $INPUT_SHA" \\',
    "  -H 'Content-Type: image/png' \\",
    "  -H 'Accept: image/png' \\",
    '  --data-binary "@$INPUT" \\',
    "  --output rgba-output.png \\",
    "  --dump-header rgba-output.headers",
  ].join("\n"),
);

async function load() {
  error.value = "";
  try {
    status.value = await api.realesrgan();
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "高清化集群状态加载失败";
    throw cause;
  }
}
async function copy(value: string) {
  await window.navigator.clipboard.writeText(value);
  ElMessage.success("已复制");
}
function gib(value: number | null) {
  return value == null ? "--" : (value / 1024).toFixed(1) + " GiB";
}
function taskTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
function shortId(value: string) {
  return value.length > 24 ? value.slice(0, 20) + "…" : value;
}
const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page esr-workspace">
    <div class="page-heading workspace-heading">
      <div>
        <div class="workspace-kicker">INFERENCE CONTROL / REAL-ESRGAN</div>
        <h1>AI 高清化工作台</h1>
        <p>围绕可用容量、异常 Worker 与排队任务组织操作，API 合同按需展开。</p>
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
      <strong>高清化控制器连接失败</strong><span>{{ error }}</span>
      <button @click="run">重试</button>
    </div>

    <section class="service-strip">
      <div class="service-verdict" :class="{ warning: !allReady }">
        <span class="status-beacon"></span>
        <div>
          <small>服务结论</small>
          <strong>{{
            orderedNodes.length
              ? readyNodes.length + " / " + orderedNodes.length + " 节点就绪"
              : "等待节点接入"
          }}</strong>
          <span>GPU only · 禁止 CPU 回退</span>
        </div>
      </div>
      <dl>
        <div>
          <dt>实时容量</dt>
          <dd>{{ status?.capacity ?? 0 }}</dd>
          <small>槽位</small>
        </div>
        <div>
          <dt>使用中</dt>
          <dd>{{ activeSlots }}</dd>
          <small>推理</small>
        </div>
        <div>
          <dt>统一队列</dt>
          <dd>{{ status?.queue_depth ?? 0 }}/{{ status?.max_queue ?? 64 }}</dd>
          <small>120 秒超时</small>
        </div>
        <div>
          <dt>活动任务</dt>
          <dd>{{ activeTasks }}</dd>
          <small>排队 + 执行</small>
        </div>
      </dl>
      <div class="model-lock">
        <small>锁定运行合同</small><strong>Anime 6B · FP16</strong>
        <span>tile 256 · pad 16 · x4 后原尺寸回采样</span>
      </div>
    </section>

    <section v-if="issueNodes.length" class="issue-rail">
      <header>
        <span>需要处理</span
        ><strong>{{ issueNodes.length }} 个 Worker 异常</strong>
      </header>
      <button
        v-for="node in issueNodes"
        :key="node.id"
        type="button"
        @click="selectedNodeId = node.id"
      >
        <b>{{ node.name }}</b
        ><span>{{ node.ready ? "ERROR" : "UNREADY" }}</span>
        <small>{{ node.last_error ?? "Worker 尚未通过 Ready 检查" }}</small>
      </button>
    </section>

    <section class="worker-console">
      <div class="worker-index">
        <header class="section-bar">
          <div>
            <small>WORKER INDEX</small>
            <h2>GPU Worker · {{ orderedNodes.length }}</h2>
          </div>
          <code>{{ status?.image_version ?? "realesrgan-worker-1.0.0" }}</code>
        </header>
        <div class="worker-columns">
          <span>Worker</span><span>状态</span><span>负载</span><span>显存</span>
        </div>
        <button
          v-for="node in orderedNodes"
          :key="node.id"
          type="button"
          class="worker-row"
          :class="{
            selected: selectedNode?.id === node.id,
            unhealthy: !node.ready,
          }"
          @click="selectedNodeId = node.id"
        >
          <span class="worker-name"
            ><i></i><b>{{ node.name }}</b
            ><small>{{ node.id }}</small></span
          >
          <span class="worker-state">{{
            node.active ? "推理中" : node.ready ? "READY" : "UNREADY"
          }}</span>
          <span>{{ node.active }} / {{ node.capacity }}</span>
          <span
            >{{ gib(node.vram_free_mb) }} / {{ gib(node.vram_total_mb) }}</span
          >
        </button>
        <p v-if="!orderedNodes.length" class="workspace-empty">
          尚无高清化 Worker
        </p>
      </div>

      <aside v-if="selectedNode" class="worker-inspector">
        <header>
          <div>
            <small>SELECTED WORKER</small>
            <h2>{{ selectedNode.name }}</h2>
            <code>{{ selectedNode.id }}</code>
          </div>
          <span :class="{ bad: !selectedNode.ready }">{{
            selectedNode.ready ? "READY" : "UNREADY"
          }}</span>
        </header>
        <p v-if="selectedNode.last_error" class="worker-error">
          {{ selectedNode.last_error }}
        </p>
        <dl class="worker-evidence">
          <div>
            <dt>GPU</dt>
            <dd>{{ selectedNode.device ?? "未探测" }}</dd>
          </div>
          <div>
            <dt>PyTorch / CUDA</dt>
            <dd>
              {{ selectedNode.pytorch ?? "--" }} /
              {{ selectedNode.cuda_runtime ?? "--" }}
            </dd>
          </div>
          <div>
            <dt>Python</dt>
            <dd>{{ selectedNode.python ?? "--" }}</dd>
          </div>
          <div>
            <dt>精度 / 参数</dt>
            <dd>
              {{ selectedNode.precision ?? "FP16" }} ·
              {{ selectedNode.tile ?? 256 }} / {{ selectedNode.tile_pad ?? 16 }}
            </dd>
          </div>
          <div>
            <dt>空闲 / 总显存</dt>
            <dd>
              {{ gib(selectedNode.vram_free_mb) }} /
              {{ gib(selectedNode.vram_total_mb) }}
            </dd>
          </div>
          <div>
            <dt>回收后空闲</dt>
            <dd>
              {{
                gib(
                  selectedNode.last_metrics.vram_free_after_mb ??
                    selectedNode.vram_free_mb,
                )
              }}
            </dd>
          </div>
          <div>
            <dt>最近额外峰值</dt>
            <dd>{{ selectedNode.last_metrics.peak_additional_mb ?? 0 }} MiB</dd>
          </div>
          <div>
            <dt>当前槽位</dt>
            <dd>{{ selectedNode.active }} / {{ selectedNode.capacity }}</dd>
          </div>
        </dl>
        <details>
          <summary>查看全部最近指标</summary>
          <pre>{{ JSON.stringify(selectedNode.last_metrics, null, 2) }}</pre>
        </details>
      </aside>
      <aside v-else class="worker-inspector empty-inspector">
        选择 Worker 查看运行证据
      </aside>
    </section>

    <section class="task-ledger">
      <header class="section-bar">
        <div>
          <small>TASK LEDGER</small>
          <h2>高清化任务</h2>
          <p>统一调度队列每 10 秒同步，保留最近 200 条记录。</p>
        </div>
        <div class="task-tools">
          <span>{{ status?.tasks.length ?? 0 }} 条</span>
          <div class="scope-switch">
            <button
              :class="{ active: taskScope === 'all' }"
              @click="taskScope = 'all'"
            >
              全部
            </button>
            <button
              :class="{ active: taskScope === 'active' }"
              @click="taskScope = 'active'"
            >
              活动 {{ activeTasks }}
            </button>
            <button
              :class="{ active: taskScope === 'failed' }"
              @click="taskScope = 'failed'"
            >
              失败 {{ failedTasks }}
            </button>
          </div>
        </div>
      </header>
      <div class="task-table-wrap">
        <table v-if="visibleTasks.length">
          <thead>
            <tr>
              <th>提交时间</th>
              <th>Request ID</th>
              <th>状态</th>
              <th>计算节点</th>
              <th>输入</th>
              <th>强度</th>
              <th>排队</th>
              <th>推理</th>
              <th>防重</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="task in visibleTasks"
              :key="task.request_id + '-' + task.created_at"
            >
              <td>{{ taskTime(task.created_at) }}</td>
              <td>
                <code :title="task.request_id">{{
                  shortId(task.request_id)
                }}</code
                ><small :title="task.input_sha256"
                  >{{ task.input_sha256.slice(0, 10) }}…</small
                >
              </td>
              <td>
                <span class="task-status" :class="task.status.toLowerCase()">{{
                  task.status
                }}</span
                ><small v-if="task.error_code">{{ task.error_code }}</small>
              </td>
              <td>{{ task.node ?? "等待调度" }}</td>
              <td>{{ task.width }} × {{ task.height }}</td>
              <td>{{ task.strength }}</td>
              <td>
                {{ task.queue_ms == null ? "--" : task.queue_ms + " ms" }}
              </td>
              <td>
                {{
                  task.processing_ms == null ? "--" : task.processing_ms + " ms"
                }}
              </td>
              <td>{{ task.cache }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="workspace-empty">当前范围暂无高清化任务。</div>
      </div>
    </section>

    <details class="api-drawer">
      <summary>
        <span
          ><small>INTEGRATION</small
          ><strong>LiClip API 与像素合同</strong></span
        >
        <code>POST {{ enhanceUrl }}</code>
      </summary>
      <div class="api-body">
        <div class="api-actions">
          <p>请求体和成功响应体均为原始 PNG 字节，不使用 JSON / Base64。</p>
          <button class="secondary" @click="copy(curlExample)">
            复制 curl
          </button>
        </div>
        <pre>{{ curlExample }}</pre>
        <dl class="contract-list">
          <div>
            <dt>认证与防重</dt>
            <dd>X-API-Key、Idempotency-Key、X-Input-SHA256 均必填</dd>
          </div>
          <div>
            <dt>像素合同</dt>
            <dd>输出宽高等于输入，8-bit RGBA，Alpha 逐像素完全相同</dd>
          </div>
          <div>
            <dt>响应追踪</dt>
            <dd>
              X-Compute-Node、X-Queue-Ms、X-Processing-Ms、X-Output-SHA256
            </dd>
          </div>
          <div>
            <dt>Ready 检查</dt>
            <dd>{{ readyUrl }}</dd>
          </div>
          <div>
            <dt>模型 SHA-256</dt>
            <dd>{{ status?.model_sha256 ?? "--" }}</dd>
          </div>
        </dl>
      </div>
    </details>
  </div>
</template>

<style scoped>
.esr-workspace {
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
.worker-inspector header small,
.api-drawer summary small {
  color: var(--a);
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.16em;
}
.service-strip {
  display: grid;
  grid-template-columns: minmax(250px, 1.1fr) minmax(440px, 2fr) minmax(
      230px,
      1fr
    );
  min-height: 104px;
  border: 1px solid var(--l);
  border-radius: 10px;
  background: var(--p);
  overflow: hidden;
}
.service-verdict {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 22px;
  border-right: 1px solid var(--l);
}
.status-beacon {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: var(--g);
  box-shadow: 0 0 0 5px rgba(85, 215, 168, 0.1);
}
.warning .status-beacon {
  background: var(--w);
}
.service-verdict div,
.model-lock {
  display: grid;
  align-content: center;
  gap: 5px;
}
.service-verdict small,
.service-verdict span,
.model-lock small,
.model-lock span,
.service-strip dt {
  color: var(--m);
  font-size: 10px;
}
.service-verdict strong,
.model-lock strong {
  color: var(--t);
  font-size: 15px;
}
.service-strip > dl {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  margin: 0;
}
.service-strip > dl div {
  display: grid;
  align-content: center;
  gap: 3px;
  padding: 0 16px;
  border-right: 1px solid var(--l);
}
.service-strip dd {
  margin: 0;
  color: #f4fbfc;
  font-size: 20px;
  font-weight: 750;
}
.service-strip dl small {
  color: var(--m);
  font-size: 9px;
}
.model-lock {
  padding: 18px 20px;
  background: rgba(97, 215, 223, 0.025);
}
.issue-rail {
  display: grid;
  grid-template-columns: 180px repeat(3, minmax(0, 1fr));
  border: 1px solid rgba(255, 124, 140, 0.25);
  border-radius: 10px;
  background: rgba(255, 124, 140, 0.035);
  overflow: hidden;
}
.issue-rail header,
.issue-rail button {
  display: grid;
  align-content: center;
  gap: 5px;
  padding: 14px 16px;
  border: 0;
  border-right: 1px solid var(--l);
  background: transparent;
  text-align: left;
}
.issue-rail header span,
.issue-rail button span {
  color: var(--b);
  font-size: 10px;
}
.issue-rail header strong,
.issue-rail button b {
  color: var(--t);
}
.issue-rail button {
  grid-template-columns: 1fr auto;
  cursor: pointer;
}
.issue-rail button small {
  grid-column: 1/-1;
  color: var(--m);
}
.worker-console {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(340px, 0.8fr);
  min-height: 430px;
  border: 1px solid var(--l);
  border-radius: 10px;
  background: var(--p);
  overflow: hidden;
}
.worker-index {
  min-width: 0;
  border-right: 1px solid var(--l);
}
.section-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 72px;
  padding: 0 18px;
  border-bottom: 1px solid var(--l);
}
.section-bar h2,
.worker-inspector h2 {
  margin: 4px 0 0;
  color: var(--t);
  font-size: 16px;
}
.section-bar p {
  margin: 4px 0 0;
  color: var(--m);
  font-size: 11px;
}
.section-bar code {
  color: var(--a);
  font-size: 10px;
}
.worker-columns,
.worker-row {
  display: grid;
  grid-template-columns:
    minmax(220px, 1.4fr) minmax(90px, 0.6fr) minmax(80px, 0.5fr)
    minmax(160px, 0.9fr);
  align-items: center;
  gap: 14px;
  padding: 0 18px;
}
.worker-columns {
  min-height: 34px;
  color: #68818b;
  background: #0c161c;
  font-size: 10px;
}
.worker-row {
  width: 100%;
  min-height: 68px;
  border: 0;
  border-top: 1px solid var(--l);
  color: #a9bec5;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.worker-row:hover,
.worker-row.selected {
  background: rgba(97, 215, 223, 0.045);
}
.worker-row.selected {
  box-shadow: inset 2px 0 var(--a);
}
.worker-name {
  display: grid;
  grid-template-columns: 20px 1fr;
  min-width: 0;
}
.worker-name i {
  grid-row: 1/3;
  width: 8px;
  height: 8px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--g);
}
.unhealthy .worker-name i {
  background: var(--b);
}
.worker-name b {
  color: var(--t);
}
.worker-name small {
  color: var(--m);
}
.worker-state {
  color: var(--g);
  font-size: 10px;
  font-weight: 800;
}
.unhealthy .worker-state {
  color: var(--b);
}
.worker-inspector {
  padding: 20px;
  background: #0c171d;
}
.worker-inspector > header {
  display: flex;
  justify-content: space-between;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--l);
}
.worker-inspector header code {
  color: var(--m);
  font-size: 10px;
}
.worker-inspector header > span {
  align-self: flex-start;
  padding: 5px 8px;
  border-radius: 999px;
  color: var(--g);
  background: rgba(85, 215, 168, 0.1);
  font-size: 10px;
}
.worker-inspector header > span.bad,
.worker-error {
  color: var(--b);
}
.worker-error {
  padding: 10px;
  background: rgba(255, 124, 140, 0.06);
  font-size: 11px;
}
.worker-evidence,
.contract-list {
  display: grid;
  gap: 0;
  margin: 14px 0 0;
  border: 1px solid var(--l);
  border-radius: 8px;
  overflow: hidden;
}
.worker-evidence div,
.contract-list div {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--l);
}
.worker-evidence dt,
.worker-evidence dd,
.contract-list dt,
.contract-list dd {
  margin: 0;
  font-size: 10px;
}
.worker-evidence dt,
.contract-list dt {
  color: var(--m);
}
.worker-evidence dd,
.contract-list dd {
  overflow-wrap: anywhere;
  color: #b8cbd1;
}
.worker-inspector details {
  margin-top: 14px;
  color: var(--a);
  font-size: 11px;
}
.worker-inspector pre {
  max-height: 160px;
  overflow: auto;
  color: #b8cbd1;
}
.empty-inspector {
  display: grid;
  place-items: center;
  color: var(--m);
}
.task-ledger,
.api-drawer {
  border: 1px solid var(--l);
  border-radius: 10px;
  background: var(--p);
  overflow: hidden;
}
.task-tools {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--a);
  font-size: 11px;
}
.scope-switch {
  display: flex;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--l);
  border-radius: 7px;
}
.scope-switch button {
  padding: 6px 9px;
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
.task-table-wrap {
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  white-space: nowrap;
}
th,
td {
  padding: 11px 13px;
  border-bottom: 1px solid var(--l);
  color: #b8cbd1;
  font-size: 11px;
  text-align: left;
}
th {
  color: #68818b;
  background: #0c161c;
  font-weight: 500;
}
td small {
  display: block;
  margin-top: 3px;
  color: var(--m);
}
.task-status {
  display: inline-block;
  min-width: 68px;
  padding: 4px 6px;
  border-radius: 5px;
  color: #b8cbd1;
  background: var(--r);
  text-align: center;
}
.task-status.succeeded {
  color: var(--g);
}
.task-status.running {
  color: #79bbff;
}
.task-status.queued {
  color: #c1a4ff;
}
.task-status.failed {
  color: var(--b);
}
.api-drawer > summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 72px;
  padding: 0 18px;
  cursor: pointer;
}
.api-drawer summary span {
  display: grid;
  gap: 4px;
}
.api-drawer summary strong {
  color: var(--t);
}
.api-drawer summary code {
  color: var(--a);
  font-size: 11px;
}
.api-body {
  padding: 0 18px 18px;
  border-top: 1px solid var(--l);
}
.api-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.api-actions p {
  color: var(--m);
  font-size: 11px;
}
.api-body > pre {
  padding: 14px;
  border-radius: 7px;
  color: #b8cbd1;
  background: #071015;
  overflow: auto;
  font-size: 11px;
  line-height: 1.55;
}
.workspace-empty {
  padding: 30px;
  color: var(--m);
  text-align: center;
}
@media (max-width: 1100px) {
  .service-strip {
    grid-template-columns: 1fr;
  }
  .service-strip > * {
    border-bottom: 1px solid var(--l);
  }
  .worker-console {
    grid-template-columns: 1fr;
  }
  .worker-index {
    border-right: 0;
  }
  .issue-rail {
    grid-template-columns: 160px 1fr;
  }
}
@media (max-width: 760px) {
  .service-strip > dl {
    grid-template-columns: 1fr 1fr;
  }
  .worker-columns {
    display: none;
  }
  .worker-row {
    grid-template-columns: 1fr 1fr;
    padding: 12px 16px;
  }
  .issue-rail {
    grid-template-columns: 1fr;
  }
  .section-bar,
  .api-drawer > summary,
  .api-actions {
    align-items: flex-start;
    flex-direction: column;
    padding: 14px;
  }
  .task-tools {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
