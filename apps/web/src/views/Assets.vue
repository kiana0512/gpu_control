<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type {
  AssetJobInfo,
  AssetProcessingOverview,
  AssetWorkerInfo,
} from "../types";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const overview = ref<AssetProcessingOverview | null>(null);
const error = ref("");
const jobType = ref<
  "ALL" | "UV_PROCESS_V2" | "RETOPOLOGY_AUDIT" | "RETOPOLOGY_PROCESS_V1"
>("ALL");
const jobState = ref<"ALL" | "ACTIVE" | "REVIEW" | "SUCCEEDED" | "FAILED">(
  "ALL",
);
const jobSearch = ref("");
const selectedJob = ref<AssetJobInfo | null>(null);
const cancellingJobId = ref("");

const workers = computed(() => overview.value?.workers ?? []);
const filteredJobs = computed(() => {
  const rows = overview.value?.jobs ?? [];
  const needle = jobSearch.value.trim().toLowerCase();
  return rows.filter((job) => {
    const typeMatches =
      jobType.value === "ALL" ||
      job.job_type === jobType.value ||
      (jobType.value === "UV_PROCESS_V2" && job.job_type === "UV_UNWRAP");
    const stateMatches =
      jobState.value === "ALL" ||
      (jobState.value === "ACTIVE" &&
        ["QUEUED", "CLAIMED", "RUNNING"].includes(job.status)) ||
      (jobState.value === "REVIEW" &&
        ["WAITING_REVIEW", "REVIEW_REJECTED"].includes(job.status)) ||
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

function workerState(worker: AssetWorkerInfo) {
  if (worker.status !== "ONLINE") return "心跳离线";
  return `${worker.current_jobs} / ${worker.max_concurrency} 槽位使用中`;
}

function jobTypeLabel(value: string) {
  if (value === "UV_PROCESS_V2") return "PBR UV";
  if (value === "RETOPOLOGY_AUDIT") return "拓扑审计";
  if (value === "RETOPOLOGY_PROCESS_V1") return "AI 重拓扑";
  if (value === "UV_UNWRAP") return "UV 兼容接口";
  return value;
}

function jobApiPath(value: string) {
  if (value === "UV_PROCESS_V2" || value === "UV_UNWRAP")
    return "/api/v1/assets/uv/process";
  if (value === "RETOPOLOGY_AUDIT")
    return "/api/v1/assets/retopology/audit";
  if (value === "RETOPOLOGY_PROCESS_V1")
    return "/api/v1/assets/retopology/process";
  return "未登记 API";
}

function jobTypeCount(value: typeof jobType.value) {
  const rows = overview.value?.jobs ?? [];
  if (value === "ALL") return rows.length;
  return rows.filter(
    (job) =>
      job.job_type === value ||
      (value === "UV_PROCESS_V2" && job.job_type === "UV_UNWRAP"),
  ).length;
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    QUEUED: "排队中",
    CLAIMED: "已领取",
    RUNNING: "执行中",
    SUCCEEDED: "已成功",
    WAITING_REVIEW: "等待人工复核",
    REVIEW_REJECTED: "复核已驳回",
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

function stageLabel(value: string) {
  const labels: Record<string, string> = {
    QUEUED: "等待可用 Worker",
    CLAIMED: "Worker 已领取任务",
    RUNNING: "正在执行",
    RETOPOLOGY_AGENT_PLANNING: "AI 正在分析模型并制定重拓扑方案",
    RETOPOLOGY_GENERATING: "正在生成重拓扑候选",
    RETOPOLOGY_RENDERING: "正在生成三模型四视图",
    RETOPOLOGY_AUDITING: "正在执行拓扑与轮廓审计",
    WAITING_REVIEW: "等待人工四视图复核",
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
    const url = URL.createObjectURL(await api.assetArtifact(job.job_id, artifact.id));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = artifact.filename;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
      { type: "warning", confirmButtonText: "确认取消", cancelButtonText: "返回" },
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
        <h1>Blender PBR UV 与重拓扑</h1>
        <p>真实 Worker 心跳 · 多任务并发 · 阶段进度与 ETA · 多视角复核 · 原子交付</p>
        <div class="task-plane-switch" aria-label="任务平面">
          <router-link to="/jobs">GPU 推理任务</router-link>
          <router-link class="active" to="/asset-processing">CPU 资产任务</router-link>
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
        <strong>与 GPU 推理任务完全隔离</strong>
        <p>
          UV 任务只有五项交付物全部通过 Blender 与 FBX 回读 QA
          后才会发布；重拓扑候选必须完成源指纹、严格审计和三模型四视图复核，禁止自动冒充最终游戏低模。
        </p>
      </div>
      <span>{{ activeJobs }} 个资产任务处理中</span>
    </section>

    <div class="asset-summary">
      <section>
        <span>在线 Worker</span
        ><strong>{{ overview?.summary.online_workers ?? 0 }}</strong
        ><small>{{ workers.length }} 个已登记</small>
      </section>
      <section>
        <span>CPU 并发槽位</span
        ><strong>{{ overview?.summary.total_slots ?? 0 }}</strong
        ><small>{{ overview?.summary.used_slots ?? 0 }} 个正在使用</small>
      </section>
      <section>
        <span>等待复核</span
        ><strong>{{ overview?.summary.waiting_review ?? 0 }}</strong
        ><small>拓扑四视图人工门禁</small>
      </section>
      <section>
        <span>UV 交付规则</span><strong>5 / 5</strong
        ><small>任一缺失则整批拒绝</small>
      </section>
    </div>

    <div class="asset-layout">
      <section class="asset-card worker-card">
        <header>
          <div>
            <h2>CPU Worker</h2>
            <p>按真实心跳、CPU、内存和租约动态限流</p>
          </div>
          <span>{{ overview?.summary.online_workers ?? 0 }} 在线</span>
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
            <span>CPU / Blender</span
            ><strong
              >{{ worker.cpu_count }} 核 · {{ worker.blender_version }}</strong
            >
          </div>
          <em
            >{{ workerState(worker)
            }}<small>{{ worker.skill_version }}</small></em
          >
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
          <span>v3</span>
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
            <dt>状态</dt>
            <dd>
              {{ overview?.contracts.uv.status ?? "/api/v1/assets/jobs/{job_id}" }}
            </dd>
          </div>
          <div>
            <dt>实时事件</dt>
            <dd>
              {{ overview?.contracts.uv.events ?? "/api/v1/assets/jobs/{job_id}/events" }}
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
            :class="{ active: jobType === 'RETOPOLOGY_PROCESS_V1' }"
            @click="jobType = 'RETOPOLOGY_PROCESS_V1'"
          >
            AI 重拓扑 {{ jobTypeCount("RETOPOLOGY_PROCESS_V1") }}
          </button>
        </div>
        </div>
        <div class="asset-filter-row">
          <strong>任务状态</strong>
          <div class="asset-filter" role="group" aria-label="资产任务状态">
            <button :class="{ active: jobState === 'ALL' }" @click="jobState = 'ALL'">全部</button>
            <button :class="{ active: jobState === 'ACTIVE' }" @click="jobState = 'ACTIVE'">排队 / 执行</button>
            <button :class="{ active: jobState === 'REVIEW' }" @click="jobState = 'REVIEW'">等待复核</button>
            <button :class="{ active: jobState === 'SUCCEEDED' }" @click="jobState = 'SUCCEEDED'">已交付</button>
            <button :class="{ active: jobState === 'FAILED' }" @click="jobState = 'FAILED'">异常 / 取消</button>
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
          ><span>Worker</span><span>进度</span><span>操作</span>
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
          <span>{{ job.worker_id ?? "尚未分配" }}</span>
          <div class="asset-progress">
            <i><b :style="{ width: `${job.progress}%` }"></b></i
            ><small>{{ Math.round(job.progress) }}% · {{ job.stage_message }}</small>
            <em
              >{{ timingSummary(job) }}</em
            >
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
        <span>筛选后 {{ filteredJobs.length }} 条 · 第 {{ currentPage }} / {{ pageCount }} 页</span>
        <label>
          每页
          <select v-model.number="pageSize">
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
        </label>
        <button class="secondary" :disabled="currentPage <= 1" @click="currentPage--">上一页</button>
        <button class="secondary" :disabled="currentPage >= pageCount" @click="currentPage++">下一页</button>
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
        <header>
          <div>
            <h2>{{ selectedJob.external_asset_id }}</h2>
            <p>
              {{ selectedJob.job_id }} ·
              {{ jobTypeLabel(selectedJob.job_type) }}
            </p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            @click="closeJob"
          >
            ×
          </button>
        </header>
        <div class="drawer-content">
          <section class="asset-detail-grid">
            <div>
              <span>状态</span
              ><strong>{{ statusLabel(selectedJob.status) }}</strong>
            </div>
            <div>
              <span>Worker</span
              ><strong>{{ selectedJob.worker_id ?? "尚未分配" }}</strong>
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
              <span>当前阶段</span><strong>{{ stageLabel(selectedJob.stage) }}</strong>
            </div>
            <div>
              <span>提交 API</span><strong>{{ jobApiPath(selectedJob.job_type) }}</strong>
            </div>
            <div>
              <span>时间</span
              ><strong>{{ timingSummary(selectedJob) }}</strong>
            </div>
          </section>
          <section class="asset-stage-message">
            <h3>实时进度说明</h3>
            <p>{{ selectedJob.stage_message }}</p>
            <small>轮询状态为最终事实；SSE 用于低延迟阶段提示并支持断线续传。</small>
          </section>
          <section>
            <h3>输入 SHA-256</h3>
            <code class="asset-hash">{{ selectedJob.input_sha256 }}</code>
          </section>
          <section v-if="selectedJob.job_type === 'RETOPOLOGY_PROCESS_V1'">
            <h3>用户端人工复核</h3>
            <p>
              调度中心只记录状态、诊断与制品清单，不在这里执行批准或驳回。
              三模型四视图及其 SHA-256 会通过任务 API 返回给提交方，由用户端展示并回传复核决定。
            </p>
          </section>
          <section>
            <h3>原子交付物（{{ selectedJob.artifacts.length }}）</h3>
            <div v-if="selectedJob.artifacts.length" class="asset-artifacts">
              <div v-for="artifact in selectedJob.artifacts" :key="artifact.id">
                <button
                  class="asset-artifact-download"
                  @click="downloadArtifact(selectedJob, artifact)"
                >
                  {{ artifact.filename }}
                </button>
                <small
                  >{{ readableSize(artifact.size_bytes) }} ·
                  {{ artifact.kind }}</small
                >
                <code>{{ artifact.sha256 }}</code>
              </div>
            </div>
            <p v-else>任务尚未发布最终交付物。</p>
          </section>
          <section v-if="selectedJob.error">
            <h3>诊断</h3>
            <p>
              {{ selectedJob.error.code }} · {{ selectedJob.error.message }}
            </p>
          </section>
        </div>
      </aside>
    </div>
  </div>
</template>
