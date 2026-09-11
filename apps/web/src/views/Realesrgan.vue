<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { useAutoRefresh } from "../composables/useAutoRefresh";

import { compareNodes } from "../nodePresentation";

type Status = Awaited<ReturnType<typeof api.realesrgan>>;
const status = ref<Status | null>(null);
const error = ref("");
const orderedNodes = computed(() => [...(status.value?.nodes ?? [])].sort(compareNodes));
const allReady = computed(() => orderedNodes.value.length > 0 && readyNodes.value.length === orderedNodes.value.length);
const origin = window.location.origin;
const enhanceUrl = computed(
  () => `${origin}${status.value?.api.enhance ?? "/api/v1/realesrgan/enhance?strength=0.7"}`,
);
const readyUrl = computed(
  () => `${origin}${status.value?.api.ready ?? "/api/v1/realesrgan/ready"}`,
);
const readyNodes = computed(
  () => status.value?.nodes.filter((node) => node.ready) ?? [],
);
const curlExample = computed(
  () => `INPUT=rgba-input.png
INPUT_SHA=$(sha256sum "$INPUT" | awk '{print $1}')
IDEM=$(printf 'RealESRGAN_x4plus_anime_6B\\0%s\\0%s' '0.7' "$INPUT_SHA" | sha256sum | awk '{print $1}')

curl --fail-with-body \\
  --cacert GPU_CONTROL_LAN_CA.crt \\
  -X POST '${enhanceUrl.value}' \\
  -H "X-API-Key: \${GPU_CONTROL_API_KEY}" \\
  -H "X-Request-ID: liclip-esrgan-001" \\
  -H "Idempotency-Key: $IDEM" \\
  -H "X-Input-SHA256: $INPUT_SHA" \\
  -H 'Content-Type: image/png' \\
  -H 'Accept: image/png' \\
  --data-binary "@$INPUT" \\
  --output rgba-output.png \\
  --dump-header rgba-output.headers`,
);

async function load() {
  error.value = "";
  try {
    status.value = await api.realesrgan();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "高清化集群状态加载失败";
    throw cause;
  }
}

async function copy(value: string) {
  await window.navigator.clipboard.writeText(value);
  ElMessage.success("已复制");
}

function gib(value: number | null) {
  return value == null ? "--" : `${(value / 1024).toFixed(1)} GiB`;
}

function taskTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function shortId(value: string) {
  return value.length > 24 ? `${value.slice(0, 20)}…` : value;
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page realesrgan-page">
    <div class="page-heading">
      <div>
        <h1>Real-ESRGAN AI 高清化</h1>
        <p>4090 统一排队与调度 · 每节点独立槽位 · RGBA / Alpha 原样保留</p>
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
        ><button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>高清化控制器连接失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="summary-grid">
      <article>
        <small>集群状态</small><strong :class="allReady ? 'ok' : 'bad'">
          {{ orderedNodes.length ? `${readyNodes.length} / ${orderedNodes.length} 节点就绪` : "等待节点接入" }}
        </strong><span>禁止 CPU 回退</span>
      </article>
      <article>
        <small>实时容量</small><strong>{{ status?.capacity ?? 0 }} 个槽位</strong
        ><span>使用中 {{ status?.nodes.reduce((sum, node) => sum + node.active, 0) ?? 0 }}</span>
      </article>
      <article>
        <small>统一队列</small><strong>{{ status?.queue_depth ?? 0 }} / {{ status?.max_queue ?? 64 }}</strong
        ><span>排队超时 120 秒</span>
      </article>
      <article>
        <small>锁定模型</small><strong>Anime 6B · FP16</strong
        ><span>tile 256 · pad 16 · x4 后原尺寸回采样</span>
      </article>
    </section>

    <section class="panel">
      <header>
        <div><h2>GPU Worker · {{ orderedNodes.length }} 台</h2><p>每次请求结束释放中间张量并清理 CUDA 缓存。</p></div>
        <code>{{ status?.image_version ?? "realesrgan-worker-1.0.0" }}</code>
      </header>
      <div class="node-grid">
        <article v-for="node in orderedNodes" :key="node.id" :class="{ offline: !node.ready }">
          <div class="node-title">
            <span class="health-dot" :class="node.ready ? 'online' : 'offline'"></span>
            <span><strong>{{ node.name }}</strong><small>{{ node.id }}</small></span>
            <b>{{ node.active ? "推理中" : node.ready ? "READY" : "UNREADY" }}</b>
          </div>
          <dl>
            <div><dt>GPU</dt><dd>{{ node.device ?? "未探测" }}</dd></div>
            <div><dt>PyTorch / CUDA</dt><dd>{{ node.pytorch ?? "--" }} / {{ node.cuda_runtime ?? "--" }}</dd></div>
            <div><dt>空闲 / 总显存</dt><dd>{{ gib(node.vram_free_mb) }} / {{ gib(node.vram_total_mb) }}</dd></div>
            <div><dt>最近额外峰值</dt><dd>{{ node.last_metrics.peak_additional_mb ?? 0 }} MiB</dd></div>
            <div><dt>回收后空闲</dt><dd>{{ gib(node.last_metrics.vram_free_after_mb ?? node.vram_free_mb) }}</dd></div>
            <div><dt>参数</dt><dd>FP16 · {{ node.tile ?? 256 }} / {{ node.tile_pad ?? 16 }}</dd></div>
          </dl>
          <p v-if="node.last_error" class="node-error">{{ node.last_error }}</p>
        </article>
      </div>
    </section>

    <section class="panel task-panel">
      <header>
        <div><h2>高清化任务列表</h2><p>与 4090 统一调度队列每 10 秒同步，保留最近 200 条记录。</p></div>
        <span class="task-count">{{ status?.tasks.length ?? 0 }} 条</span>
      </header>
      <div class="task-table-wrap">
        <table v-if="status?.tasks.length">
          <thead><tr><th>提交时间</th><th>Request ID</th><th>状态</th><th>计算节点</th><th>输入</th><th>强度</th><th>排队</th><th>推理</th><th>防重</th></tr></thead>
          <tbody>
            <tr v-for="task in status.tasks" :key="`${task.request_id}-${task.created_at}`">
              <td>{{ taskTime(task.created_at) }}</td>
              <td><code :title="task.request_id">{{ shortId(task.request_id) }}</code></td>
              <td><span class="task-status" :class="task.status.toLowerCase()">{{ task.status }}</span><small v-if="task.error_code">{{ task.error_code }}</small></td>
              <td>{{ task.node ?? "等待调度" }}</td>
              <td>{{ task.width }} × {{ task.height }}</td>
              <td>{{ task.strength }}</td>
              <td>{{ task.queue_ms == null ? "--" : `${task.queue_ms} ms` }}</td>
              <td>{{ task.processing_ms == null ? "--" : `${task.processing_ms} ms` }}</td>
              <td>{{ task.cache }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-tasks">暂无高清化任务；首次 API 调用后会自动同步到这里。</div>
      </div>
    </section>

    <section class="panel api-panel">
      <header>
        <div><h2>LiClip API</h2><p>请求体和成功响应体均为原始 PNG 字节，不使用 JSON / Base64。</p></div>
        <button class="secondary" @click="copy(curlExample)">复制 curl</button>
      </header>
      <div class="endpoint">
        <b>POST</b><code>{{ enhanceUrl }}</code>
      </div>
      <pre>{{ curlExample }}</pre>
      <div class="contract-grid">
        <div><strong>认证与防重</strong><span>X-API-Key、Idempotency-Key、X-Input-SHA256 均必填</span></div>
        <div><strong>像素合同</strong><span>输出宽高等于输入，8-bit RGBA，Alpha 逐像素完全相同</span></div>
        <div><strong>响应追踪</strong><span>X-Compute-Node、X-Queue-Ms、X-Processing-Ms、X-Output-SHA256</span></div>
        <div><strong>Ready 检查</strong><span>{{ readyUrl }}</span></div>
      </div>
      <p class="model-hash">模型 SHA-256：<code>{{ status?.model_sha256 ?? "--" }}</code></p>
    </section>
  </div>
</template>

<style scoped>
.realesrgan-page { display: grid; gap: 18px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
.summary-grid article, .panel { border: 1px solid var(--line-color, #2a3042); background: var(--panel-bg, #111522); border-radius: 12px; }
.summary-grid article { padding: 18px; display: grid; gap: 7px; }
.summary-grid small, .summary-grid span { color: #8e99b2; }
.summary-grid strong { font-size: 21px; color: #f3f5fb; }
.summary-grid strong.ok { color: #3dd6a3; }
.summary-grid strong.bad { color: #ffb15b; }
.panel { overflow: hidden; }
.panel > header { min-height: 74px; padding: 0 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #2a3042; }
.panel h2 { margin: 0 0 5px; font-size: 16px; color: #f3f5fb; }
.panel header p { margin: 0; color: #8e99b2; font-size: 13px; }
.panel header code { color: #b894ff; }
.node-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; background: #2a3042; }
.node-grid article { background: #111522; padding: 18px 20px; }
.node-grid article.offline { opacity: 0.72; }
.node-title { display: flex; align-items: center; gap: 10px; }
.node-title > span:nth-child(2) { display: grid; gap: 2px; flex: 1; }
.node-title strong { color: #f3f5fb; }
.node-title small { color: #75819a; }
.node-title b { color: #3dd6a3; font-size: 12px; }
.offline .node-title b { color: #ff6f7d; }
dl { margin: 17px 0 0; display: grid; grid-template-columns: 1fr 1fr; gap: 12px 22px; }
dl div { display: grid; gap: 3px; min-width: 0; }
dt { color: #75819a; font-size: 12px; }
dd { margin: 0; color: #dce1ee; overflow-wrap: anywhere; }
.node-error { color: #ff8b96; font-size: 12px; margin: 14px 0 0; }
.api-panel { padding-bottom: 20px; }
.endpoint { margin: 18px 20px 0; padding: 13px 15px; border: 1px solid #343b51; border-radius: 8px; display: flex; gap: 14px; align-items: center; }
.endpoint b { color: #3dd6a3; font-size: 12px; }
.endpoint code { color: #e4e8f4; overflow-wrap: anywhere; }
pre { margin: 14px 20px; padding: 16px; border-radius: 8px; background: #090c14; color: #c7d0e5; overflow: auto; line-height: 1.55; }
.contract-grid { margin: 0 20px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.contract-grid div { border: 1px solid #2a3042; border-radius: 8px; padding: 12px; display: grid; gap: 5px; }
.contract-grid strong { color: #e9edf7; font-size: 13px; }
.contract-grid span { color: #8e99b2; font-size: 12px; overflow-wrap: anywhere; }
.model-hash { margin: 16px 20px 0; color: #8e99b2; font-size: 12px; }
.model-hash code { color: #b9c4dc; }
.task-count { color: #b894ff; font-size: 13px; }
.task-table-wrap { overflow: auto; }
table { width: 100%; border-collapse: collapse; white-space: nowrap; }
th, td { padding: 12px 14px; border-bottom: 1px solid #252b3b; text-align: left; color: #cbd2e3; font-size: 12px; }
th { color: #7f8aa3; font-weight: 500; background: #0e121d; }
td code { color: #aeb9d2; }
td small { display: block; margin-top: 4px; color: #ff8b96; }
.task-status { display: inline-block; min-width: 72px; padding: 4px 7px; border-radius: 5px; text-align: center; background: #2a3042; color: #cbd2e3; font-size: 11px; font-weight: 700; }
.task-status.succeeded { background: rgba(61, 214, 163, .12); color: #3dd6a3; }
.task-status.running { background: rgba(87, 164, 255, .14); color: #77b8ff; }
.task-status.queued { background: rgba(184, 148, 255, .14); color: #c2a3ff; }
.task-status.failed { background: rgba(255, 111, 125, .13); color: #ff7e8a; }
.empty-tasks { padding: 36px 20px; text-align: center; color: #75819a; }
@media (max-width: 1100px) { .summary-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 760px) { .summary-grid, .node-grid, .contract-grid { grid-template-columns: 1fr; } }
</style>
