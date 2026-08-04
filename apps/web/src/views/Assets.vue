<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type {
  AssetJobInfo,
  AssetProcessingOverview,
  AssetWorkerInfo,
} from "../types";
import {
  assetDeliveryPolicy,
  assetExecutionTarget,
  assetProgressMessage,
  assetWorkerState,
  isSubstanceWorker,
  substanceGpuStateSummary,
} from "../assetPresentation";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const overview = ref<AssetProcessingOverview | null>(null);
const error = ref("");
const jobType = ref<
  | "ALL"
  | "UV_PROCESS_V2"
  | "RETOPOLOGY_AUDIT"
  | "RETOPOLOGY_PROCESS_V2"
  | "SUBSTANCE_BAKE_V1"
>("ALL");
const jobState = ref<"ALL" | "ACTIVE" | "SUCCEEDED" | "FAILED">("ALL");
const jobSearch = ref("");
const selectedJob = ref<AssetJobInfo | null>(null);
const cancellingJobId = ref("");

const workers = computed(() => {
  const raw = overview.value?.workers ?? [];
  const bakerRows = raw.filter(isSubstanceWorker);
  if (!bakerRows.length) return raw;
  const instanceRows = bakerRows.filter((worker) =>
    worker.id.startsWith("asset-worker-3090-b-windows-"),
  );
  const effectiveRows = instanceRows.length ? instanceRows : bakerRows;
  const onlineRows = effectiveRows.filter(
    (worker) => worker.status === "ONLINE",
  );
  const newest = [...effectiveRows].sort((left, right) =>
    (right.last_heartbeat_at ?? "").localeCompare(left.last_heartbeat_at ?? ""),
  )[0];
  const aggregate: AssetWorkerInfo = {
    ...newest,
    id: "asset-worker-3090-b-windows",
    display_name: "3090-B Windows Substance Baker",
    status: onlineRows.length ? "ONLINE" : "OFFLINE",
    current_jobs: onlineRows.reduce(
      (sum, worker) => sum + worker.current_jobs,
      0,
    ),
    max_concurrency: onlineRows.reduce(
      (sum, worker) => sum + worker.max_concurrency,
      0,
    ),
    cpu_count: Math.max(...effectiveRows.map((worker) => worker.cpu_count)),
  };
  return [...raw.filter((worker) => !isSubstanceWorker(worker)), aggregate];
});
const onlineWorkerCount = computed(
  () => workers.value.filter((worker) => worker.status === "ONLINE").length,
);
const totalAssetSlots = computed(() =>
  workers.value
    .filter((worker) => worker.status === "ONLINE")
    .reduce((sum, worker) => sum + worker.max_concurrency, 0),
);
const usedAssetSlots = computed(() =>
  workers.value.reduce((sum, worker) => sum + worker.current_jobs, 0),
);
const filteredJobs = computed(() => {
  const rows = overview.value?.jobs ?? [];
  const needle = jobSearch.value.trim().toLowerCase();
  return rows.filter((job) => {
    const typeMatches =
      jobType.value === "ALL" ||
      job.job_type === jobType.value ||
      (jobType.value === "UV_PROCESS_V2" && job.job_type === "UV_UNWRAP") ||
      (jobType.value === "RETOPOLOGY_PROCESS_V2" &&
        job.job_type === "RETOPOLOGY_PROCESS_V1");
    const stateMatches =
      jobState.value === "ALL" ||
      (jobState.value === "ACTIVE" &&
        ["QUEUED", "CLAIMED", "RUNNING"].includes(job.status)) ||
      (jobState.value === "SUCCEEDED" && job.status === "SUCCEEDED") ||
      (jobState.value === "FAILED" &&
        ["FAILED", "CANCELLED"].includes(job.status));
    const searchMatches =
      !needle ||
      [
        job.external_asset_id,
        job.job_id,
        job.client_id,
        job.source_filename,
        job.worker_id ?? "",
      ].some((value) => value.toLowerCase().includes(needle));
    return typeMatches && stateMatches && searchMatches;
  });
});
const currentPage = ref(1);
const pageSize = ref(20);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(filteredJobs.value.length / pageSize.value)),
);
const jobs = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredJobs.value.slice(start, start + pageSize.value);
});
watch([jobType, jobState, jobSearch, pageSize], () => {
  currentPage.value = 1;
});
watch(pageCount, (count) => {
  if (currentPage.value > count) currentPage.value = count;
});
const counts = computed(() => overview.value?.summary.counts ?? {});
const activeJobs = computed(
  () =>
    (counts.value.QUEUED ?? 0) +
    (counts.value.CLAIMED ?? 0) +
    (counts.value.RUNNING ?? 0),
);
const substanceGpuSummary = computed(() =>
  substanceGpuStateSummary(overview.value?.substance_gpu),
);
const selectedEvidenceArtifacts = computed(() =>
  (selectedJob.value?.artifacts ?? []).filter(
    (artifact) =>
      artifact.kind.startsWith("view_") ||
      ["comparison", "reference_images"].includes(artifact.kind),
  ),
);
const selectedCodexArtifacts = computed(() =>
  (selectedJob.value?.artifacts ?? []).filter((artifact) =>
    ["agent_prompt", "agent_events", "agent_plan"].includes(artifact.kind),
  ),
);
const selectedPrimaryArtifacts = computed(() =>
  (selectedJob.value?.artifacts ?? []).filter(
    (artifact) =>
      !selectedEvidenceArtifacts.value.includes(artifact) &&
      !selectedCodexArtifacts.value.includes(artifact),
  ),
);
const selectedDeliverableArtifacts = computed(() =>
  selectedPrimaryArtifacts.value.filter(
    (artifact) =>
      ["blend", "fbx", "candidate_blend", "candidate_fbx"].includes(
        artifact.kind,
      ) || /\.(blend|fbx|zip|png)$/i.test(artifact.filename),
  ),
);
const selectedTechnicalArtifacts = computed(() => {
  const visibleIds = new Set(
    selectedDeliverableArtifacts.value.map((artifact) => artifact.id),
  );
  return (selectedJob.value?.artifacts ?? []).filter(
    (artifact) => !visibleIds.has(artifact.id),
  );
});
const selectedUserRequest = computed(() => {
  const value = selectedJob.value?.options.user_request;
  return typeof value === "string" && value.trim()
    ? value
    : "未提供额外迭代要求";
});

async function load() {
  error.value = "";
  try {
    overview.value = await api.assetProcessing();
    if (selectedJob.value) {
      selectedJob.value =
        overview.value.jobs.find(
          (job) => job.job_id === selectedJob.value?.job_id,
        ) ?? null;
    }
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "资产处理状态加载失败";
    throw cause;
  }
}

function codexProbeClass(worker: AssetWorkerInfo) {
  return (worker.codex_probe_status || "NOT_RUN").toLowerCase();
}

function codexProbeLabel(worker: AssetWorkerInfo) {
  if (worker.codex_probe_status === "HEALTHY") return "真实探针正常";
  if (worker.codex_probe_status === "RUNNING") return "正在验证";
  if (worker.codex_probe_status === "NOT_RUN") return "等待首次探针";
  return worker.codex_error_code || worker.codex_probe_status || "状态未知";
}

function retopoflowProbeClass(worker: AssetWorkerInfo) {
  if (!worker.retopoflow_probe_status) return "pending";
  return worker.retopoflow_probe_status === "HEALTHY" ? "healthy" : "unhealthy";
}

function retopoflowProbeLabel(worker: AssetWorkerInfo) {
  if (!worker.retopoflow_probe_status) return "RetopoFlow 等待新版心跳";
  if (worker.retopoflow_probe_status === "HEALTHY") {
    const revision = worker.retopoflow_revision?.slice(0, 8) ?? "未知提交";
    return `RetopoFlow ${worker.retopoflow_version} · operator 已调用 · ${revision}`;
  }
  if (worker.retopoflow_probe_status === "NOT_RUN")
    return "RetopoFlow 正在实测";
  return `RetopoFlow 不可用 · ${worker.retopoflow_error_code ?? worker.retopoflow_probe_status}`;
}

function jobTypeLabel(value: string) {
  if (value === "UV_PROCESS_V2") return "PBR UV";
  if (value === "RETOPOLOGY_AUDIT") return "拓扑审计";
  if (["RETOPOLOGY_PROCESS_V1", "RETOPOLOGY_PROCESS_V2"].includes(value))
    return value === "RETOPOLOGY_PROCESS_V2" ? "AI 重拓扑 V6" : "AI 重拓扑 V5";
  if (value === "SUBSTANCE_BAKE_V1") return "Substance PBR 烘焙";
  if (value === "UV_UNWRAP") return "UV 兼容接口";
  return value;
}

function jobApiPath(value: string) {
  if (value === "UV_PROCESS_V2" || value === "UV_UNWRAP")
    return "/api/v1/assets/uv/process";
  if (value === "RETOPOLOGY_AUDIT") return "/api/v1/assets/retopology/audit";
  if (["RETOPOLOGY_PROCESS_V1", "RETOPOLOGY_PROCESS_V2"].includes(value))
    return "/api/v1/assets/retopology/process";
  if (value === "SUBSTANCE_BAKE_V1") return "/api/v1/assets/bake/process";
  return "未登记 API";
}

function jobTypeCount(value: typeof jobType.value) {
  const rows = overview.value?.jobs ?? [];
  if (value === "ALL") return rows.length;
  return rows.filter(
    (job) =>
      job.job_type === value ||
      (value === "UV_PROCESS_V2" && job.job_type === "UV_UNWRAP") ||
      (value === "RETOPOLOGY_PROCESS_V2" &&
        job.job_type === "RETOPOLOGY_PROCESS_V1"),
  ).length;
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    QUEUED: "排队中",
    CLAIMED: "已领取",
    RUNNING: "执行中",
    SUCCEEDED: "已成功",
    WAITING_REVIEW: "历史待收口",
    REVIEW_REJECTED: "历史已驳回",
    FAILED: "失败",
    CANCELLED: "已取消",
  };
  return labels[value] ?? value;
}

const terminalStatuses = new Set([
  "SUCCEEDED",
  "WAITING_REVIEW",
  "REVIEW_REJECTED",
  "FAILED",
  "CANCELLED",
]);

function isTerminal(job: AssetJobInfo) {
  return terminalStatuses.has(job.status);
}

function elapsedSeconds(job: AssetJobInfo) {
  if (!job.started_at) return 0;
  const startedAt = Date.parse(job.started_at);
  const terminalAt = job.finished_at ?? job.timing.last_progress_at;
  const endedAt =
    isTerminal(job) && terminalAt ? Date.parse(terminalAt) : Date.now();
  if (!Number.isFinite(startedAt) || !Number.isFinite(endedAt)) {
    return job.timing.elapsed_seconds;
  }
  return Math.max(0, Math.floor((endedAt - startedAt) / 1000));
}

function timingSummary(job: AssetJobInfo) {
  const elapsed = readableDuration(elapsedSeconds(job));
  if (isTerminal(job)) return `总耗时 ${elapsed}`;
  const remaining = readableDuration(job.timing.estimated_remaining_seconds);
  return `已用 ${elapsed} · 剩余约 ${remaining}`;
}

function formatDateTime(value: string | null) {
  if (!value) return "--";
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp);
}

function diagnosticSummary(job: AssetJobInfo) {
  if (!job.error) return "";
  const message = (job.error.message ?? "任务未生成有效交付物").trim();
  return message.length > 220 ? `${message.slice(0, 220)}…` : message;
}

function stageLabel(value: string) {
  const labels: Record<string, string> = {
    QUEUED: "等待可用 Worker",
    CLAIMED: "Worker 已领取任务",
    RUNNING: "正在执行",
    RETOPOLOGY_AGENT_PLANNING: "AI 正在分析模型并制定重拓扑方案",
    RETOPOLOGY_GENERATING: "正在生成重拓扑候选",
    RETOPOLOGY_RENDERING: "正在生成三模型四视图",
    RETOPOLOGY_AUDITING: "正在执行拓扑与轮廓审计",
    BAKING_TEXTURE_TRANSFER: "正在投射高模材质贴图",
    BAKING_GEOMETRY_MAPS: "正在生成几何派生贴图",
    BAKING_NORMALS: "正在生成 DX、GL 与世界空间法线",
    WAITING_REVIEW: "历史状态待迁移",
    SUCCEEDED: "交付完成",
    FAILED: "执行失败",
    CANCELLED: "任务已取消",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

function openJob(job: AssetJobInfo) {
  selectedJob.value = job;
}

function closeJob() {
  selectedJob.value = null;
}

async function downloadArtifact(
  job: AssetJobInfo,
  artifact: AssetJobInfo["artifacts"][number],
) {
  try {
    const url = URL.createObjectURL(
      await api.assetArtifact(job.job_id, artifact.id),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifact.filename;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "制品下载失败");
  }
}

function readableSize(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function readableDuration(value: number | null | undefined) {
  if (value == null) return "等待动态估算";
  if (value < 60) return `${Math.max(0, Math.round(value))} 秒`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes} 分 ${seconds} 秒`;
}

function canCancel(job: AssetJobInfo) {
  return ![
    "SUCCEEDED",
    "WAITING_REVIEW",
    "REVIEW_REJECTED",
    "FAILED",
    "CANCELLED",
  ].includes(job.status);
}

async function cancelAssetJob(job: AssetJobInfo) {
  try {
    await ElMessageBox.confirm(
      `确认取消资产任务 ${job.external_asset_id}？`,
      "取消资产任务",
      {
        type: "warning",
        confirmButtonText: "确认取消",
        cancelButtonText: "返回",
      },
    );
    cancellingJobId.value = job.job_id;
    await api.cancelAssetJob(job.job_id);
    ElMessage.success("资产任务取消请求已提交");
    await run();
  } catch (cause) {
    if (cause !== "cancel" && cause !== "close") {
      ElMessage.error(cause instanceof Error ? cause.message : "取消失败");
    }
  } finally {
    cancellingJobId.value = "";
  }
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page assets-page">
    <div class="page-heading asset-heading">
      <div>
        <div class="eyebrow">统一资产处理平面</div>
        <h1>统一 PBR 资产处理</h1>
        <p>粗糙度生成 · Blender UV / 重拓扑 · Windows Substance 烘焙</p>
        <div class="task-plane-switch" aria-label="任务平面">
          <router-link to="/jobs">GPU 推理任务</router-link>
          <router-link class="active" to="/asset-processing"
            >资产任务</router-link
          >
          <router-link to="/clients">查看 API 调用示例</router-link>
        </div>
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
      <strong>资产处理平面同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="asset-notice">
      <div>
        <strong>队列独立；3090-B 物理 GPU 按任务互斥</strong>
        <p>
          UV 与重拓扑使用 CPU Worker；Substance Baker 与 ComfyUI 共享同一张
          3090-B，但不会并行争抢 GPU。生产 PBR 到达后取得下一轮优先权，等待当前
          ComfyUI 帧安全结束再切换；显存缓存驻留本身不等于任务占用。
        </p>
      </div>
      <span>{{ substanceGpuSummary }} · {{ activeJobs }} 个资产任务处理中</span>
    </section>

    <div class="asset-summary">
      <section>
        <span>在线 Worker</span><strong>{{ onlineWorkerCount }}</strong
        ><small>{{ workers.length }} 个已登记</small>
      </section>
      <section>
        <span>逻辑 Worker 槽位</span><strong>{{ totalAssetSlots }}</strong
        ><small>{{ usedAssetSlots }} 个使用中；Baker 共享 1 张 3090-B</small>
      </section>
      <section>
        <span>严格 QA 失败</span
        ><strong>{{ overview?.summary.qa_failed ?? 0 }}</strong
        ><small>不发布交付物，保留诊断证据</small>
      </section>
      <section>
        <span>烘焙输出</span><strong>10 图</strong
        ><small>颜色、PBR、AO、法线与几何图</small>
      </section>
    </div>

    <div class="asset-layout">
      <section class="asset-card worker-card">
        <header>
          <div>
            <h2>资产 Worker</h2>
            <p>CPU 槽位与 Windows Baker 进程分列；Baker 共享 3090-B 物理 GPU</p>
          </div>
          <span>{{ onlineWorkerCount }} 在线</span>
        </header>
        <div
          v-for="worker in workers"
          :key="worker.id"
          class="asset-worker-row"
        >
          <div class="worker-identity">
            <i :class="{ offline: worker.status !== 'ONLINE' }"></i>
            <div>
              <strong>{{ worker.display_name }}</strong
              ><small>{{ worker.hostname }} · {{ worker.node_id }}</small>
            </div>
          </div>
          <div>
            <span>{{
              isSubstanceWorker(worker)
                ? "Windows / Substance · 3090-B"
                : "CPU / Blender"
            }}</span
            ><strong
              >{{ worker.cpu_count }} 核 · {{ worker.blender_version }}</strong
            >
          </div>
          <em>
            {{ assetWorkerState(worker) }}
            <small>{{ worker.skill_version }}</small>
            <small
              v-if="!isSubstanceWorker(worker)"
              class="worker-codex"
              :class="codexProbeClass(worker)"
            >
              Codex {{ worker.codex_cli_version ?? "未发现" }} ·
              {{ codexProbeLabel(worker) }}
            </small>
            <small
              v-if="!isSubstanceWorker(worker)"
              class="worker-codex"
              :class="retopoflowProbeClass(worker)"
            >
              {{ retopoflowProbeLabel(worker) }}
            </small>
            <small v-else class="worker-codex healthy">
              Windows 原生多进程 · {{ substanceGpuSummary }}
            </small>
          </em>
        </div>
        <div v-if="!workers.length && !refreshing" class="asset-empty">
          尚无 Asset Worker 上报心跳
        </div>
      </section>

      <section class="asset-card contract-card">
        <header>
          <div>
            <h2>外部 API 契约</h2>
            <p>幂等提交、轮询状态、原子取件</p>
          </div>
          <span>v4</span>
        </header>
        <dl>
          <div>
            <dt>UV</dt>
            <dd>
              {{ overview?.contracts.uv.submit ?? "/api/v1/assets/uv/process" }}
            </dd>
          </div>
          <div>
            <dt>重拓扑</dt>
            <dd>
              {{
                overview?.contracts.retopology_process.submit ??
                "/api/v1/assets/retopology/process"
              }}
            </dd>
          </div>
          <div>
            <dt>粗糙度</dt>
            <dd>
              {{
                overview?.contracts.roughness.submit ??
                "/api/v1/services/modelview-roughness"
              }}
            </dd>
          </div>
          <div>
            <dt>PBR 烘焙</dt>
            <dd>
              {{
                overview?.contracts.substance_bake.submit ??
                "/api/v1/assets/bake/process"
              }}
            </dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>
              {{
                overview?.contracts.uv.status ?? "/api/v1/assets/jobs/{job_id}"
              }}
            </dd>
          </div>
          <div>
            <dt>实时事件</dt>
            <dd>
              {{
                overview?.contracts.uv.events ??
                "/api/v1/assets/jobs/{job_id}/events"
              }}
            </dd>
          </div>
          <div>
            <dt>取消</dt>
            <dd>POST /api/v1/assets/jobs/{job_id}/cancel</dd>
          </div>
          <div>
            <dt>重拓扑输入</dt>
            <dd>BLEND + 0~32 张多视角参考图 + 迭代要求</dd>
          </div>
        </dl>
      </section>
    </div>

    <section class="asset-card asset-jobs-card">
      <header>
        <div>
          <h2>资产任务</h2>
          <p>按提交 API 分类；每个模型一个父任务，交付物与审计信息进入详情</p>
        </div>
      </header>
      <div class="asset-filter-bar">
        <div class="asset-filter-row">
          <strong>API 分类</strong>
          <div class="asset-filter" role="group" aria-label="资产任务类型">
            <button
              :class="{ active: jobType === 'ALL' }"
              @click="jobType = 'ALL'"
            >
              全部 {{ jobTypeCount("ALL") }}
            </button>
            <button
              :class="{ active: jobType === 'UV_PROCESS_V2' }"
              @click="jobType = 'UV_PROCESS_V2'"
            >
              UV {{ jobTypeCount("UV_PROCESS_V2") }}
            </button>
            <button
              :class="{ active: jobType === 'RETOPOLOGY_AUDIT' }"
              @click="jobType = 'RETOPOLOGY_AUDIT'"
            >
              拓扑审计 {{ jobTypeCount("RETOPOLOGY_AUDIT") }}
            </button>
            <button
              :class="{ active: jobType === 'RETOPOLOGY_PROCESS_V2' }"
              @click="jobType = 'RETOPOLOGY_PROCESS_V2'"
            >
              AI 重拓扑 {{ jobTypeCount("RETOPOLOGY_PROCESS_V2") }}
            </button>
            <button
              :class="{ active: jobType === 'SUBSTANCE_BAKE_V1' }"
              @click="jobType = 'SUBSTANCE_BAKE_V1'"
            >
              PBR 烘焙 {{ jobTypeCount("SUBSTANCE_BAKE_V1") }}
            </button>
          </div>
        </div>
        <div class="asset-filter-row">
          <strong>任务状态</strong>
          <div class="asset-filter" role="group" aria-label="资产任务状态">
            <button
              :class="{ active: jobState === 'ALL' }"
              @click="jobState = 'ALL'"
            >
              全部
            </button>
            <button
              :class="{ active: jobState === 'ACTIVE' }"
              @click="jobState = 'ACTIVE'"
            >
              排队 / 执行
            </button>
            <button
              :class="{ active: jobState === 'SUCCEEDED' }"
              @click="jobState = 'SUCCEEDED'"
            >
              已交付
            </button>
            <button
              :class="{ active: jobState === 'FAILED' }"
              @click="jobState = 'FAILED'"
            >
              异常 / 取消
            </button>
          </div>
          <input
            v-model="jobSearch"
            class="asset-search"
            type="search"
            placeholder="搜索任务、客户、文件、Worker"
            aria-label="搜索资产任务"
          />
        </div>
      </div>
      <div class="asset-job-table" role="table" aria-label="资产任务">
        <div class="asset-job-row asset-job-head" role="row">
          <span>任务 / 来源</span><span>分类 / API</span><span>状态</span
          ><span>Worker</span><span>任务时间</span><span>进度</span
          ><span>操作</span>
        </div>
        <div
          v-for="job in jobs"
          :key="job.job_id"
          class="asset-job-row"
          role="row"
        >
          <div>
            <strong>{{ job.external_asset_id }}</strong
            ><small
              >{{ job.source_filename }} · {{ job.job_id.slice(0, 8) }}</small
            >
          </div>
          <span class="asset-api-kind"
            ><strong>{{ jobTypeLabel(job.job_type) }}</strong
            ><code>{{ jobApiPath(job.job_type) }}</code></span
          >
          <span class="asset-status" :class="job.status.toLowerCase()">{{
            statusLabel(job.status)
          }}</span>
          <span>{{ assetExecutionTarget(job) }}</span>
          <div class="asset-time-stack">
            <span>提交 {{ formatDateTime(job.created_at) }}</span>
            <span>开始 {{ formatDateTime(job.started_at) }}</span>
            <span v-if="isTerminal(job)"
              >结束 {{ formatDateTime(job.finished_at) }}</span
            >
          </div>
          <div class="asset-progress">
            <i><b :style="{ width: `${job.progress}%` }"></b></i
            ><small
              >{{ Math.round(job.progress) }}% ·
              {{ assetProgressMessage(job) }}</small
            >
            <em>{{ timingSummary(job) }}</em>
          </div>
          <div class="asset-job-actions">
            <button class="secondary compact" @click="openJob(job)">
              查看
            </button>
            <button
              v-if="canCancel(job)"
              class="danger-button compact"
              :disabled="cancellingJobId === job.job_id"
              @click="cancelAssetJob(job)"
            >
              {{ cancellingJobId === job.job_id ? "取消中" : "取消" }}
            </button>
          </div>
        </div>
      </div>
      <div v-if="!jobs.length && !refreshing" class="asset-empty">
        当前筛选下暂无资产任务
      </div>
      <nav class="table-pagination" aria-label="资产任务分页">
        <span
          >筛选后 {{ filteredJobs.length }} 条 · 第 {{ currentPage }} /
          {{ pageCount }} 页</span
        >
        <label>
          每页
          <select v-model.number="pageSize">
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
        </label>
        <button
          class="secondary"
          :disabled="currentPage <= 1"
          @click="currentPage--"
        >
          上一页
        </button>
        <button
          class="secondary"
          :disabled="currentPage >= pageCount"
          @click="currentPage++"
        >
          下一页
        </button>
      </nav>
    </section>

    <div
      v-if="selectedJob"
      class="panel-backdrop maintenance-backdrop"
      @click.self="closeJob"
    >
      <aside
        class="maintenance-drawer asset-detail"
        role="dialog"
        aria-modal="true"
      >
        <header class="asset-detail-header">
          <div>
            <span class="asset-detail-kicker">{{
              jobTypeLabel(selectedJob.job_type)
            }}</span>
            <h2>{{ selectedJob.external_asset_id }}</h2>
          </div>
          <button class="icon-button" aria-label="关闭" @click="closeJob">
            ×
          </button>
        </header>
        <div class="drawer-content">
          <section class="asset-status-rail">
            <span
              class="asset-status"
              :class="selectedJob.status.toLowerCase()"
            >
              {{ statusLabel(selectedJob.status) }}
            </span>
            <div>
              <strong>{{ Math.round(selectedJob.progress) }}%</strong>
              <i><b :style="{ width: `${selectedJob.progress}%` }"></b></i>
            </div>
            <span>{{ timingSummary(selectedJob) }}</span>
          </section>
          <section class="asset-key-facts">
            <div>
              <span>执行节点</span
              ><strong>{{ assetExecutionTarget(selectedJob) }}</strong>
            </div>
            <div>
              <span>当前阶段</span
              ><strong>{{ stageLabel(selectedJob.stage) }}</strong>
            </div>
            <div>
              <span>提交时间</span
              ><strong>{{ formatDateTime(selectedJob.created_at) }}</strong>
            </div>
            <div>
              <span>开始时间</span
              ><strong>{{ formatDateTime(selectedJob.started_at) }}</strong>
            </div>
            <div>
              <span>结束时间</span
              ><strong>{{ formatDateTime(selectedJob.finished_at) }}</strong>
            </div>
            <div>
              <span>累计耗时</span
              ><strong>{{ timingSummary(selectedJob) }}</strong>
            </div>
          </section>
          <section class="asset-stage-message">
            <h3>{{ isTerminal(selectedJob) ? "处理结果" : "实时进度" }}</h3>
            <p>{{ assetProgressMessage(selectedJob) }}</p>
            <span
              class="asset-delivery-policy"
              :class="assetDeliveryPolicy(selectedJob).className"
            >
              {{ assetDeliveryPolicy(selectedJob).label }}
            </span>
          </section>
          <section v-if="selectedJob.error" class="asset-failure-summary">
            <h3>失败原因</h3>
            <strong>{{ selectedJob.error.code }}</strong>
            <p>{{ diagnosticSummary(selectedJob) }}</p>
          </section>
          <section>
            <div class="asset-section-heading">
              <div>
                <h3>最终交付</h3>
                <p>默认仅显示可直接使用的模型文件。</p>
              </div>
              <span>{{ selectedDeliverableArtifacts.length }}</span>
            </div>
            <div
              v-if="selectedDeliverableArtifacts.length"
              class="asset-deliverables"
            >
              <button
                v-for="artifact in selectedDeliverableArtifacts"
                :key="artifact.id"
                class="asset-deliverable"
                @click="downloadArtifact(selectedJob, artifact)"
              >
                <span>{{ artifact.filename }}</span>
                <small>{{ readableSize(artifact.size_bytes) }}</small>
                <b>下载</b>
              </button>
            </div>
            <p v-else class="asset-empty-delivery">尚未生成最终交付文件。</p>
          </section>
          <details class="asset-advanced">
            <summary>高级诊断</summary>
            <div class="asset-advanced-facts">
              <div>
                <span>任务 ID</span><code>{{ selectedJob.job_id }}</code>
              </div>
              <div>
                <span>输入文件</span
                ><strong>{{ selectedJob.source_filename }}</strong>
              </div>
              <div>
                <span>尝试次数</span
                ><strong>{{ selectedJob.attempt_count }}</strong>
              </div>
              <div>
                <span>提交 API</span
                ><code>{{ jobApiPath(selectedJob.job_type) }}</code>
              </div>
              <div class="wide">
                <span>输入 SHA-256</span
                ><code>{{ selectedJob.input_sha256 }}</code>
              </div>
              <div
                v-if="
                  ['RETOPOLOGY_PROCESS_V1', 'RETOPOLOGY_PROCESS_V2'].includes(
                    selectedJob.job_type,
                  )
                "
                class="wide"
              >
                <span>用户要求</span><strong>{{ selectedUserRequest }}</strong>
              </div>
            </div>
            <div
              v-if="selectedTechnicalArtifacts.length"
              class="asset-technical-list"
            >
              <button
                v-for="artifact in selectedTechnicalArtifacts"
                :key="artifact.id"
                @click="downloadArtifact(selectedJob, artifact)"
              >
                <span>{{ artifact.filename }}</span>
                <small
                  >{{ artifact.kind }} ·
                  {{ readableSize(artifact.size_bytes) }}</small
                >
              </button>
            </div>
            <pre v-if="selectedJob.error">{{ selectedJob.error.message }}</pre>
          </details>
        </div>
      </aside>
    </div>
  </div>
</template>
