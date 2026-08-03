<script setup lang="ts">
import { computed, ref } from "vue";
import { api } from "../api";
import type { AssetProcessingOverview, NodeInfo } from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";
import {
  codexHealthLabel,
  codexHealthMessage,
  healthyCodexProbeAverage,
} from "../codexPresentation";

const nodes = ref<NodeInfo[]>([]);
const assets = ref<AssetProcessingOverview | null>(null);
const error = ref("");

const runtimes = computed(() =>
  nodes.value.filter(
    (node) => node.last_heartbeat_at || node.health !== "OFFLINE",
  ),
);
const healthyCount = computed(
  () =>
    runtimes.value.filter((node) => node.codex_cli?.scheduler_eligible).length,
);
const activeCount = computed(
  () => runtimes.value.filter((node) => node.codex_cli?.task?.is_active).length,
);
const authenticatedCount = computed(
  () =>
    runtimes.value.filter((node) =>
      ["AUTHENTICATED", "HEALTHY", "READY"].includes(
        node.codex_cli?.auth_status ?? "",
      ),
    ).length,
);
const averageLatency = computed(() => healthyCodexProbeAverage(runtimes.value));

const workerNode = computed(() => {
  const result = new Map<string, string>();
  for (const worker of assets.value?.workers ?? [])
    result.set(worker.id, worker.node_id);
  return result;
});
const recentExecutions = computed(() =>
  (assets.value?.jobs ?? [])
    .filter((job) =>
      ["RETOPOLOGY_AUDIT", "RETOPOLOGY_PROCESS_V1"].includes(job.job_type),
    )
    .slice(0, 8),
);

function health(node: NodeInfo) {
  return node.codex_cli?.health ?? "CHECKING";
}
function healthLabel(node: NodeInfo) {
  return codexHealthLabel(node);
}
function healthMessage(node: NodeInfo) {
  return codexHealthMessage(node);
}
function time(value: string | null | undefined) {
  if (!value) return "尚未成功";
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
  error.value = "";
  try {
    const [nodeData, assetData] = await Promise.all([
      api.nodes(),
      api.assetProcessing(100),
    ]);
    nodes.value = nodeData;
    assets.value = assetData;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Codex 状态加载失败";
    throw cause;
  }
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page codex-page">
    <div class="page-heading codex-heading">
      <div>
        <div class="eyebrow">AGENT RUNTIME OBSERVABILITY</div>
        <h1>Codex 运行中心</h1>
        <p>独立查看三台主机的安装、认证、真实调用与资产任务上下文</p>
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
      <strong>Codex 状态同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="codex-summary">
      <article>
        <span>健康运行时</span
        ><strong>{{ healthyCount }} / {{ runtimes.length }}</strong
        ><small>必须通过真实 codex exec 探针</small>
      </article>
      <article>
        <span>认证有效</span><strong>{{ authenticatedCount }}</strong
        ><small>不以“命令存在”替代登录验证</small>
      </article>
      <article>
        <span>当前执行</span><strong>{{ activeCount }}</strong
        ><small>与 GPU 推理槽完全隔离</small>
      </article>
      <article>
        <span>平均探针延迟</span
        ><strong>{{
          averageLatency == null ? "—" : `${averageLatency} ms`
        }}</strong
        ><small>最近一次真实轻量调用</small>
      </article>
    </section>

    <section class="codex-runtime-grid">
      <article
        v-for="node in runtimes"
        :key="node.id"
        class="codex-runtime-card"
        :class="health(node).toLowerCase()"
      >
        <header>
          <div class="codex-machine">
            <span class="codex-glyph">C</span>
            <div>
              <h2>{{ node.display_name }}</h2>
              <p>{{ node.id }}</p>
            </div>
          </div>
          <span class="codex-health-pill"><i></i>{{ healthLabel(node) }}</span>
        </header>
        <p class="codex-health-message">{{ healthMessage(node) }}</p>
        <div class="codex-facts">
          <div>
            <span>主机 CLI</span
            ><strong>{{ node.codex_cli?.host_version ?? "待上报" }}</strong>
          </div>
          <div>
            <span>Worker CLI</span
            ><strong>{{ node.codex_cli?.runtime_version ?? "待上报" }}</strong>
          </div>
          <div>
            <span>认证状态</span
            ><strong>{{ node.codex_cli?.auth_status ?? "CHECKING" }}</strong>
          </div>
          <div>
            <span>调用探针</span
            ><strong>{{ node.codex_cli?.probe_status ?? "NOT_RUN" }}</strong>
          </div>
        </div>
        <div
          class="codex-task-card"
          :class="{ active: node.codex_cli?.task?.is_active }"
        >
          <div class="codex-task-title">
            <span>{{ taskTitle(node) }}</span
            ><i></i>
          </div>
          <template v-if="node.codex_cli?.task">
            <strong>{{ node.codex_cli.task.external_asset_id }}</strong>
            <p>
              {{ node.codex_cli.task.stage }} · {{ node.codex_cli.task.status }}
            </p>
            <dl>
              <div>
                <dt>输入</dt>
                <dd>{{ node.codex_cli.task.input.filename }}</dd>
              </div>
              <div>
                <dt>SHA</dt>
                <dd>{{ node.codex_cli.task.input.sha256.slice(0, 16) }}…</dd>
              </div>
              <div>
                <dt>参考图</dt>
                <dd>{{ node.codex_cli.task.input.reference_view_count }} 张</dd>
              </div>
              <div>
                <dt>输出合同</dt>
                <dd>
                  {{ node.codex_cli.task.output_contract.length }} 组原子制品
                </dd>
              </div>
            </dl>
          </template>
          <template v-else>
            <strong>没有占用中的 Codex 任务</strong>
            <p>健康探针仍独立运行，不占资产任务槽。</p>
          </template>
        </div>
        <footer>最近成功：{{ time(node.codex_cli?.last_success_at) }}</footer>
      </article>
    </section>

    <section class="codex-history-panel">
      <header>
        <div>
          <h2>最近 Codex 资产执行</h2>
          <p>
            输入、阶段、Worker 与终态可追溯；完整提示词和事件保留在任务详情。
          </p>
        </div>
        <span>{{ recentExecutions.length }} 条</span>
      </header>
      <div class="codex-history-table">
        <div class="table-head">
          <span>任务 / 输入</span><span>Worker</span><span>阶段</span
          ><span>状态</span>
        </div>
        <div
          v-for="job in recentExecutions"
          :key="job.job_id"
          class="table-row"
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
          <span class="history-status" :class="job.status.toLowerCase()">{{
            statusLabel(job.status)
          }}</span>
        </div>
        <div v-if="!recentExecutions.length" class="codex-history-empty">
          尚无 Codex 资产执行记录
        </div>
      </div>
    </section>
  </div>
</template>
