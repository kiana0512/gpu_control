<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { BatchItem, JobInfo } from "../types";
import {
  assemblingAt,
  assemblyDuration,
  endToEndDuration,
  formatDateTime,
  formatDuration,
  gpuDuration,
  median,
  nodeSummary,
  publishDuration,
  queueDuration,
  serviceFor,
  statusGroup,
  taskSearchText,
  type TaskJob,
} from "../jobPresentation";
import JobsTable from "../components/JobsTable.vue";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const route = useRoute();
const jobs = ref<TaskJob[]>([]);
const loading = ref(false);
const error = ref("");
const selectedJob = ref<TaskJob | null>(null);
const batchItems = ref<BatchItem[]>([]);
const batchOffset = ref(0);
const batchItemsTotal = ref(0);
const batchLoading = ref(false);
const batchPageSize = 100;
const actionBusy = ref("");
const clientKind = ref<"production" | "test">(
  route.query.kind === "test" ? "test" : "production",
);
const query = ref("");
const serviceFilter = ref("all");
const workflowFilter = ref("all");
const apiFilter = ref("all");
const statusFilter = ref("all");
const currentPage = ref(1);
const pageSize = ref(20);

const terminalStatuses = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"];
const isBatch = computed(() => selectedJob.value?.kind === "batch");
const selectedDisplayStatus = computed(() =>
  selectedJob.value?.kind === "batch" &&
  selectedJob.value.status === "CANCELLING" &&
  selectedJob.value.error
    ? "FAILING"
    : (selectedJob.value?.status ?? ""),
);
const canCancel = computed(
  () =>
    selectedJob.value && !terminalStatuses.includes(selectedJob.value.status),
);
const canRetry = computed(
  () =>
    selectedJob.value &&
    selectedJob.value.kind !== "batch" &&
    ["FAILED", "TIMED_OUT"].includes(selectedJob.value.status),
);

const serviceOptions = computed(() => {
  const grouped = new Map<
    string,
    { key: string; label: string; count: number }
  >();
  for (const job of jobs.value) {
    const service = serviceFor(job);
    const current = grouped.get(service.key);
    if (current) current.count += 1;
    else
      grouped.set(service.key, {
        key: service.key,
        label: service.shortLabel,
        count: 1,
      });
  }
  return [...grouped.values()].sort((left, right) => right.count - left.count);
});

const workflowOptions = computed(() =>
  [...new Set(jobs.value.map((job) => job.workflow_key))].sort(),
);
const apiOptions = computed(() =>
  [...new Set(jobs.value.map((job) => serviceFor(job).api))].sort(),
);
const statusOptions = computed(() => [
  { key: "all", label: "全部状态", count: jobs.value.length },
  {
    key: "active",
    label: "正在处理",
    count: jobs.value.filter((job) => statusGroup(job) === "active").length,
  },
  {
    key: "queued",
    label: "排队 / 校验",
    count: jobs.value.filter((job) => statusGroup(job) === "queued").length,
  },
  {
    key: "succeeded",
    label: "已完成",
    count: jobs.value.filter((job) => statusGroup(job) === "succeeded").length,
  },
  {
    key: "attention",
    label: "异常 / 取消",
    count: jobs.value.filter((job) => statusGroup(job) === "attention").length,
  },
]);

const filteredJobs = computed(() => {
  const normalizedQuery = query.value.trim().toLocaleLowerCase("zh-CN");
  return jobs.value.filter((job) => {
    const service = serviceFor(job);
    return (
      (!normalizedQuery || taskSearchText(job).includes(normalizedQuery)) &&
      (serviceFilter.value === "all" || service.key === serviceFilter.value) &&
      (workflowFilter.value === "all" ||
        job.workflow_key === workflowFilter.value) &&
      (apiFilter.value === "all" || service.api === apiFilter.value) &&
      (statusFilter.value === "all" || statusGroup(job) === statusFilter.value)
    );
  });
});

const pageCount = computed(() =>
  Math.max(1, Math.ceil(filteredJobs.value.length / pageSize.value)),
);
const pagedJobs = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredJobs.value.slice(start, start + pageSize.value);
});
const visibleStart = computed(() =>
  filteredJobs.value.length ? (currentPage.value - 1) * pageSize.value + 1 : 0,
);
const visibleEnd = computed(() =>
  Math.min(currentPage.value * pageSize.value, filteredJobs.value.length),
);

const metrics = computed(() => {
  const successful = jobs.value.filter(
    (job) => statusGroup(job) === "succeeded",
  );
  return {
    total: jobs.value.length,
    active: jobs.value.filter((job) => statusGroup(job) === "active").length,
    queued: jobs.value.filter((job) => statusGroup(job) === "queued").length,
    attention: jobs.value.filter((job) => statusGroup(job) === "attention")
      .length,
    successful: successful.length,
    medianDuration: median(successful.map(endToEndDuration)),
    timedSamples: successful.filter((job) => endToEndDuration(job) !== null)
      .length,
  };
});

const selectedTimeline = computed(() => {
  if (!selectedJob.value) return [];
  const job = selectedJob.value;
  return [
    { label: "任务创建", value: job.created_at },
    { label: "校验完成", value: job.validated_at },
    { label: "进入队列", value: job.queued_at },
    { label: "GPU 开始", value: job.started_at },
    { label: "最后进度", value: job.last_progress_at },
    { label: "GPU 完成", value: job.execution_finished_at },
    { label: "开始组装", value: assemblingAt(job) },
    { label: "产物就绪", value: job.artifact_ready_at },
    { label: "任务结束", value: job.finished_at },
  ];
});

watch(
  [query, serviceFilter, workflowFilter, apiFilter, statusFilter, pageSize],
  () => {
    currentPage.value = 1;
  },
);
watch(pageCount, (count) => {
  if (currentPage.value > count) currentPage.value = count;
});

async function loadBatch(id: string, offset = batchOffset.value) {
  batchLoading.value = true;
  try {
    const [detail, page] = await Promise.all([
      api.batch(id),
      api.batchItems(id, offset, batchPageSize),
    ]);
    selectedJob.value = detail as TaskJob;
    batchItems.value = page.items;
    batchItemsTotal.value = page.total;
    batchOffset.value = page.offset;
  } finally {
    batchLoading.value = false;
  }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    jobs.value = (await api.jobs(undefined, clientKind.value)) as TaskJob[];
    const requestedJob =
      typeof route.query.job === "string" ? route.query.job : "";
    if (requestedJob && !selectedJob.value)
      selectedJob.value =
        jobs.value.find((job) => job.job_id === requestedJob) ?? null;
    if (selectedJob.value?.kind === "batch") {
      await loadBatch(selectedJob.value.job_id);
    } else if (selectedJob.value) {
      selectedJob.value =
        jobs.value.find((job) => job.job_id === selectedJob.value?.job_id) ??
        selectedJob.value;
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "任务数据加载失败";
    throw cause;
  } finally {
    loading.value = false;
  }
}

async function retry(id: string) {
  try {
    await ElMessageBox.confirm(
      "确认重试这个任务？系统不会对已提交且状态未知的 prompt 盲目重提。",
      "确认重试",
      { type: "warning" },
    );
    actionBusy.value = "retry";
    await api.retry(id);
    ElMessage.success("任务已重新排队");
    selectedJob.value = null;
    await load();
  } catch (cause) {
    if (cause !== "cancel" && cause !== "close")
      ElMessage.error(cause instanceof Error ? cause.message : "重试失败");
  } finally {
    actionBusy.value = "";
  }
}

async function cancel(id: string) {
  try {
    await ElMessageBox.confirm(
      "确认取消这个任务？运行中的 ComfyUI prompt 会被中断。",
      "确认取消任务",
      { type: "warning" },
    );
    actionBusy.value = "cancel";
    if (isBatch.value) await api.cancelBatch(id);
    else await api.cancel(id);
    ElMessage.success("取消请求已提交");
    selectedJob.value = null;
    await load();
  } catch (cause) {
    if (cause !== "cancel" && cause !== "close")
      ElMessage.error(cause instanceof Error ? cause.message : "取消失败");
  } finally {
    actionBusy.value = "";
  }
}

async function diagnostics(id: string) {
  try {
    actionBusy.value = "diagnostics";
    const blob = await api.diagnostics(id);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${id}-diagnostics.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    ElMessage.success("诊断包已开始下载");
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : "诊断包下载失败");
  } finally {
    actionBusy.value = "";
  }
}

async function selectJob(job: JobInfo) {
  selectedJob.value = job as TaskJob;
  batchItems.value = [];
  batchOffset.value = 0;
  batchItemsTotal.value = 0;
  if (job.kind === "batch") await loadBatch(job.job_id, 0);
}

async function changeClientKind(kind: "production" | "test") {
  if (clientKind.value === kind) return;
  clientKind.value = kind;
  clearFilters();
  selectedJob.value = null;
  await run();
}

async function changeBatchPage(offset: number) {
  if (!selectedJob.value || selectedJob.value.kind !== "batch") return;
  await loadBatch(
    selectedJob.value.job_id,
    Math.max(0, Math.min(offset, Math.max(0, batchItemsTotal.value - 1))),
  );
}

function clearFilters() {
  query.value = "";
  serviceFilter.value = "all";
  workflowFilter.value = "all";
  apiFilter.value = "all";
  statusFilter.value = "all";
  currentPage.value = 1;
}

function nodeDistribution(job: TaskJob) {
  const entries = Object.entries(job.node_distribution ?? {});
  return entries.length
    ? entries.map(([node, count]) => `${node}：${count} 帧`).join("，")
    : "尚未分配";
}

function shortHash(value: string | null | undefined) {
  if (!value) return "未上报";
  return value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page jobs-operations-page">
    <header class="jobs-hero">
      <div>
        <span class="hero-eyebrow">GPU CONTROL · OPERATIONS</span>
        <h1>GPU 任务运行中心</h1>
        <p>
          按业务功能、工作流与 API
          快速定位任务；时间字段只展示服务端真实上报值。
        </p>
        <nav class="view-switch" aria-label="任务与分析视图">
          <router-link to="/jobs">任务中心</router-link>
          <router-link to="/analysis">性能分析</router-link>
          <router-link to="/asset-processing">CPU 资产任务</router-link>
        </nav>
      </div>
      <div class="hero-actions">
        <span class="refresh-copy">
          <i :class="{ spinning: refreshing }"></i>
          自动刷新 10 秒
          <small>
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}
          </small>
        </span>
        <button class="secondary" :disabled="loading" @click="run">
          {{ loading ? "同步中…" : "立即刷新" }}
        </button>
      </div>
    </header>

    <div v-if="error" class="error-banner persistent-error">
      <strong>任务同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="task-metrics" aria-label="任务摘要">
      <article>
        <span>当前范围</span>
        <strong>{{ metrics.total }}</strong>
        <small>{{
          clientKind === "production" ? "真实任务" : "压力测试"
        }}</small>
      </article>
      <article>
        <span>正在处理</span>
        <strong>{{ metrics.active }}</strong>
        <small>{{ metrics.queued }} 个排队 / 校验中</small>
      </article>
      <article>
        <span>已成功</span>
        <strong>{{ metrics.successful }}</strong>
        <small>{{ metrics.attention }} 个异常或取消</small>
      </article>
      <article class="accent-metric">
        <span>成功任务端到端中位数</span>
        <strong>{{ formatDuration(metrics.medianDuration) }}</strong>
        <small>{{ metrics.timedSamples }} 个具有完整时间的样本</small>
      </article>
    </section>

    <section class="task-center">
      <div class="task-center-heading">
        <div>
          <span class="section-eyebrow">TASK CENTER</span>
          <h2>任务中心</h2>
          <p>父批次保持为一条任务，逐帧明细在详情中查看。</p>
        </div>
        <div class="scope-tabs" aria-label="任务数据范围">
          <button
            :class="{ active: clientKind === 'production' }"
            @click="changeClientKind('production')"
          >
            真实任务
          </button>
          <button
            :class="{ active: clientKind === 'test' }"
            @click="changeClientKind('test')"
          >
            测试任务
          </button>
        </div>
      </div>

      <div class="filter-panel">
        <div class="filter-row service-filter-row">
          <span class="filter-label">按功能</span>
          <button
            class="filter-chip"
            :class="{ active: serviceFilter === 'all' }"
            @click="serviceFilter = 'all'"
          >
            全部 <b>{{ jobs.length }}</b>
          </button>
          <button
            v-for="option in serviceOptions"
            :key="option.key"
            class="filter-chip"
            :class="{ active: serviceFilter === option.key }"
            @click="serviceFilter = option.key"
          >
            {{ option.label }} <b>{{ option.count }}</b>
          </button>
        </div>
        <div class="filter-grid">
          <label class="search-control">
            <span>搜索任务</span>
            <input
              v-model="query"
              type="search"
              placeholder="任务 ID、业务批次、节点、API…"
            />
          </label>
          <label>
            <span>工作流</span>
            <select v-model="workflowFilter">
              <option value="all">全部工作流</option>
              <option v-for="workflow in workflowOptions" :key="workflow">
                {{ workflow }}
              </option>
            </select>
          </label>
          <label>
            <span>API</span>
            <select v-model="apiFilter">
              <option value="all">全部 API</option>
              <option v-for="endpoint in apiOptions" :key="endpoint">
                {{ endpoint }}
              </option>
            </select>
          </label>
          <label>
            <span>状态</span>
            <select v-model="statusFilter">
              <option
                v-for="option in statusOptions"
                :key="option.key"
                :value="option.key"
              >
                {{ option.label }} · {{ option.count }}
              </option>
            </select>
          </label>
          <button class="clear-filter" @click="clearFilters">清除筛选</button>
        </div>
      </div>

      <div class="result-summary">
        <span>
          显示 <strong>{{ visibleStart }}–{{ visibleEnd }}</strong> /
          {{ filteredJobs.length }}
          条
        </span>
        <span v-if="filteredJobs.length !== jobs.length">
          已从 {{ jobs.length }} 条任务中筛选
        </span>
      </div>

      <JobsTable :jobs="pagedJobs" @select="selectJob" />

      <nav class="jobs-pagination" aria-label="GPU 任务分页">
        <span>第 {{ currentPage }} / {{ pageCount }} 页</span>
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
      class="panel-backdrop task-backdrop"
      @click.self="selectedJob = null"
    >
      <aside
        class="task-drawer"
        :class="{ 'batch-drawer': isBatch }"
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
      >
        <header>
          <div>
            <span class="eyebrow">{{
              isBatch ? "父批次详情" : "独立任务详情"
            }}</span>
            <h2>
              {{ selectedJob.external_batch_id || selectedJob.job_id }}
            </h2>
            <p>
              {{ serviceFor(selectedJob).label }} ·
              {{ serviceFor(selectedJob).api }}
            </p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            @click="selectedJob = null"
          >
            ×
          </button>
        </header>

        <div class="drawer-scroll">
          <div class="task-result" :class="selectedJob.status.toLowerCase()">
            <StatusMark :value="selectedDisplayStatus" />
            <strong>{{
              selectedJob.status === "SUCCEEDED"
                ? isBatch
                  ? "全部序列帧已校验并生成完整结果包"
                  : "任务处理成功，最终结果已返回"
                : selectedJob.status === "FAILED"
                  ? isBatch
                    ? "父批次失败，未发布不完整结果"
                    : "任务执行失败，请查看错误信息"
                  : selectedDisplayStatus === "FAILING"
                    ? "子任务失败，系统正在安全收尾；这不是用户取消"
                    : "任务状态与进度已同步"
            }}</strong>
            <span>进度 {{ selectedJob.progress.toFixed(0) }}%</span>
          </div>

          <section class="duration-summary" aria-label="任务耗时摘要">
            <article>
              <span>端到端</span>
              <strong>{{
                formatDuration(endToEndDuration(selectedJob))
              }}</strong>
            </article>
            <article>
              <span>真实排队</span>
              <strong>{{ formatDuration(queueDuration(selectedJob)) }}</strong>
            </article>
            <article>
              <span>GPU wall</span>
              <strong>{{ formatDuration(gpuDuration(selectedJob)) }}</strong>
            </article>
            <article>
              <span>产物组装</span>
              <strong>{{
                formatDuration(assemblyDuration(selectedJob))
              }}</strong>
            </article>
          </section>

          <section class="timeline-card">
            <div class="drawer-section-heading">
              <div>
                <h3>关键时间线</h3>
                <p>未上报的字段保持为空，不用其他阶段时间代替。</p>
              </div>
              <span
                >{{ selectedTimeline.filter((item) => item.value).length }} / 9
                已上报</span
              >
            </div>
            <ol class="timeline-grid">
              <li
                v-for="item in selectedTimeline"
                :key="item.label"
                :class="{ missing: !item.value }"
              >
                <i></i>
                <span>{{ item.label }}</span>
                <strong>{{ formatDateTime(item.value) }}</strong>
              </li>
            </ol>
          </section>

          <dl class="task-facts" :class="{ 'batch-facts': isBatch }">
            <dt>{{ isBatch ? "完整父批次 ID" : "完整任务 ID" }}</dt>
            <dd>
              <code>{{ selectedJob.job_id }}</code>
            </dd>
            <template v-if="isBatch">
              <dt>动画管家批次</dt>
              <dd>
                <code>{{ selectedJob.external_batch_id }}</code>
              </dd>
              <dt>序列帧进度</dt>
              <dd>
                {{ selectedJob.counts?.succeeded ?? 0 }} /
                {{ selectedJob.counts?.total ?? 0 }} 成功，
                {{ selectedJob.counts?.running ?? 0 }} 运行，
                {{ selectedJob.counts?.failed ?? 0 }} 失败
              </dd>
              <dt>节点分配</dt>
              <dd>{{ nodeDistribution(selectedJob) }}</dd>
            </template>
            <template v-else>
              <dt>执行节点</dt>
              <dd>{{ nodeSummary(selectedJob) }}</dd>
              <dt>ComfyUI Prompt</dt>
              <dd>
                <code>{{ selectedJob.prompt_id || "未上报" }}</code>
              </dd>
            </template>
            <dt>业务功能</dt>
            <dd>{{ serviceFor(selectedJob).label }}</dd>
            <dt>提交 API</dt>
            <dd>
              <code>{{ serviceFor(selectedJob).api }}</code>
            </dd>
            <dt>工作流身份</dt>
            <dd>
              <code>
                {{ selectedJob.workflow_key }} ·
                {{ selectedJob.workflow_version }}
              </code>
            </dd>
            <dt>Pipeline commit</dt>
            <dd>
              <code>{{ shortHash(selectedJob.pipeline_commit) }}</code>
            </dd>
            <dt>Pipeline SHA-256</dt>
            <dd>
              <code>{{ shortHash(selectedJob.pipeline_sha256) }}</code>
            </dd>
            <dt>最终输出节点</dt>
            <dd>{{ selectedJob.output_node || "未上报" }}</dd>
            <dt>{{ isBatch ? "累计尝试" : "尝试次数" }}</dt>
            <dd>{{ selectedJob.attempt }}</dd>
            <dt>产物发布</dt>
            <dd>{{ formatDuration(publishDuration(selectedJob)) }}</dd>
          </dl>

          <section v-if="isBatch" class="batch-items-panel">
            <div class="batch-items-heading">
              <div>
                <h3>序列帧详情</h3>
                <p>父批次保持一条记录，逐帧状态在此追溯。</p>
              </div>
              <span v-if="batchLoading">正在同步…</span>
            </div>
            <div class="batch-items-scroll">
              <table>
                <thead>
                  <tr>
                    <th>序号</th>
                    <th>输入路径</th>
                    <th>状态</th>
                    <th>节点</th>
                    <th>尝试</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in batchItems" :key="item.ordinal">
                    <td>{{ item.ordinal }}</td>
                    <td :title="item.input_relative_path">
                      <code>{{ item.input_relative_path }}</code>
                      <small v-if="item.error">
                        {{ item.error.code }} · {{ item.error.message }}
                      </small>
                    </td>
                    <td><StatusMark :value="item.status" /></td>
                    <td>{{ item.node_id || "未分配" }}</td>
                    <td>{{ item.attempts }}</td>
                  </tr>
                  <tr v-if="!batchItems.length">
                    <td colspan="5" class="empty">尚无已物化帧</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="batch-pagination">
              <span>
                {{ batchItemsTotal ? batchOffset + 1 : 0 }}–{{
                  Math.min(batchOffset + batchItems.length, batchItemsTotal)
                }}
                / {{ batchItemsTotal }}
              </span>
              <button
                class="secondary"
                :disabled="batchOffset === 0 || batchLoading"
                @click="changeBatchPage(batchOffset - batchPageSize)"
              >
                上一页
              </button>
              <button
                class="secondary"
                :disabled="
                  batchOffset + batchPageSize >= batchItemsTotal || batchLoading
                "
                @click="changeBatchPage(batchOffset + batchPageSize)"
              >
                下一页
              </button>
            </div>
          </section>

          <section
            v-if="isBatch && selectedJob.artifacts?.length"
            class="batch-artifacts"
          >
            <h3>完整结果归档</h3>
            <a
              v-for="artifact in selectedJob.artifacts"
              :key="artifact.id"
              :href="artifact.download_url"
              target="_blank"
              rel="noopener"
            >
              <strong>{{ artifact.filename }}</strong>
              <span
                >{{ (artifact.size_bytes / 1024 / 1024).toFixed(1) }} MiB</span
              >
              <code>SHA-256 {{ artifact.sha256 }}</code>
            </a>
          </section>

          <section v-if="selectedJob.error" class="task-error">
            <h3>失败原因</h3>
            <strong>{{ selectedJob.error.code }}</strong>
            <p>{{ selectedJob.error.message }}</p>
          </section>
          <section v-else class="task-help">
            <h3>
              {{ selectedJob.status === "SUCCEEDED" ? "结果说明" : "操作说明" }}
            </h3>
            <p>
              {{
                selectedJob.status === "SUCCEEDED"
                  ? isBatch
                    ? "结果 ZIP 仅在全部帧、路径、顺序、SHA-256 和 Alpha 校验通过后发布给动画管家。"
                    : "同步图片 API 返回最终图片；管理端保留任务记录与诊断信息。"
                  : "状态每 10 秒自动刷新。运行中的任务可取消，独立失败任务可安全重试。"
              }}
            </p>
          </section>
        </div>

        <footer>
          <button
            v-if="!isBatch"
            class="secondary"
            :disabled="Boolean(actionBusy)"
            @click="diagnostics(selectedJob.job_id)"
          >
            {{ actionBusy === "diagnostics" ? "正在生成…" : "下载诊断包" }}
          </button>
          <button
            v-if="canRetry"
            class="primary"
            :disabled="Boolean(actionBusy)"
            @click="retry(selectedJob.job_id)"
          >
            重试任务
          </button>
          <button
            v-if="canCancel"
            class="danger-button"
            :disabled="Boolean(actionBusy)"
            @click="cancel(selectedJob.job_id)"
          >
            取消任务
          </button>
        </footer>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.jobs-operations-page {
  --task-panel: #11141f;
  --task-panel-raised: #171a27;
  --task-line: #2b3040;
  --task-muted: #929bad;
  --task-pink: #e34eb2;
  --task-purple: #a550f2;
  padding-bottom: 50px;
}

.jobs-hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 22px;
}

.hero-eyebrow,
.section-eyebrow {
  display: block;
  margin-bottom: 8px;
  color: #e660bc;
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
}

.jobs-hero h1 {
  margin: 0;
  color: #fbfaff;
  font-size: clamp(30px, 3vw, 42px);
  font-weight: 780;
  letter-spacing: -0.035em;
  line-height: 1.08;
}

.jobs-hero p {
  max-width: 760px;
  margin: 11px 0 0;
  color: #9ea7b8;
  font-size: 15px;
  line-height: 1.6;
}

.view-switch {
  display: flex;
  gap: 6px;
  margin-top: 18px;
}

.view-switch a {
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  padding: 0 14px;
  color: #a9b1c1;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 680;
  text-decoration: none;
}

.view-switch a.router-link-active {
  color: #fff3fc;
  border-color: rgb(223 76 178 / 28%);
  background: rgb(223 76 178 / 10%);
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: none;
}

.refresh-copy {
  display: grid;
  grid-template-columns: 10px auto;
  align-items: center;
  gap: 2px 8px;
  color: #d1d6df;
  font-size: 13px;
  font-weight: 650;
}

.refresh-copy i {
  grid-row: 1 / span 2;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #28d6a4;
  box-shadow: 0 0 10px rgb(40 214 164 / 55%);
}

.refresh-copy i.spinning {
  background: #df4cb4;
  animation: task-pulse 900ms infinite alternate;
}

.refresh-copy small {
  color: #7e8798;
  font-size: 12px;
  font-weight: 500;
}

@keyframes task-pulse {
  to {
    opacity: 0.35;
  }
}

.task-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  overflow: hidden;
  margin-bottom: 20px;
  border: 1px solid var(--task-line);
  border-radius: 14px;
  background: var(--task-panel);
}

.task-metrics article {
  min-height: 120px;
  padding: 21px 24px;
  border-right: 1px solid var(--task-line);
}

.task-metrics article:last-child {
  border-right: 0;
}

.task-metrics span,
.task-metrics small {
  display: block;
  color: var(--task-muted);
  font-size: 13px;
}

.task-metrics strong {
  display: block;
  margin: 10px 0 8px;
  color: #f7f7fb;
  font-size: 30px;
  line-height: 1;
}

.task-metrics .accent-metric {
  background: linear-gradient(
    135deg,
    rgb(156 65 223 / 15%),
    rgb(223 76 178 / 8%)
  );
}

.task-metrics .accent-metric strong {
  color: #f36cc3;
}

.task-center {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  padding: 22px;
  border: 1px solid var(--task-line);
  border-radius: 15px;
  background: #0e111a;
}

.task-center-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 18px;
}

.task-center-heading h2 {
  margin: 0;
  color: #f7f6fb;
  font-size: 24px;
}

.task-center-heading p {
  margin: 6px 0 0;
  color: #929bad;
  font-size: 14px;
}

.filter-panel {
  margin-bottom: 16px;
  overflow: hidden;
  border: 1px solid var(--task-line);
  border-radius: 12px;
  background: #121620;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 62px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--task-line);
}

.filter-label {
  margin-right: 4px;
  color: #929bad;
  font-size: 13px;
  font-weight: 700;
}

.filter-chip {
  min-height: 34px;
  padding: 0 12px;
  color: #aeb6c5;
  border: 1px solid #303647;
  border-radius: 8px;
  background: #161b27;
  font-size: 13px;
  cursor: pointer;
}

.filter-chip b {
  margin-left: 5px;
  color: #737e91;
  font-weight: 650;
}

.filter-chip.active {
  color: #fff4fc;
  border-color: rgb(223 76 178 / 45%);
  background: linear-gradient(
    110deg,
    rgb(160 75 235 / 23%),
    rgb(223 76 178 / 16%)
  );
}

.filter-chip.active b {
  color: #f08ccf;
}

.filter-grid {
  display: grid;
  grid-template-columns:
    minmax(260px, 1.35fr) repeat(3, minmax(170px, 0.8fr))
    auto;
  align-items: end;
  gap: 12px;
  padding: 15px 16px;
}

.filter-grid label {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.filter-grid label > span {
  color: #8f98a9;
  font-size: 12px;
  font-weight: 650;
}

.filter-grid input,
.filter-grid select,
.jobs-pagination select {
  width: 100%;
  height: 40px;
  min-width: 0;
  padding: 0 12px;
  color: #e3e6ed;
  border: 1px solid #343b4d;
  border-radius: 8px;
  outline: 0;
  background: #0d111a;
  font-size: 13px;
}

.filter-grid input:focus,
.filter-grid select:focus {
  border-color: #c352e4;
  box-shadow: 0 0 0 3px rgb(195 82 228 / 10%);
}

.clear-filter {
  height: 40px;
  padding: 0 14px;
  color: #c9d0db;
  border: 1px solid #343b4d;
  border-radius: 8px;
  background: #171c28;
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
}

.result-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 42px;
  color: #8f98aa;
  font-size: 13px;
}

.result-summary strong {
  color: #eceef4;
}

.jobs-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 16px;
  color: #929bad;
  font-size: 13px;
}

.jobs-pagination label {
  display: flex;
  align-items: center;
  gap: 7px;
}

.jobs-pagination select {
  width: 74px;
  height: 38px;
}

.jobs-pagination button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.task-drawer {
  width: min(760px, 100vw);
}

.task-drawer.batch-drawer {
  width: min(1080px, 100vw);
}

.task-drawer > header h2 {
  max-width: 820px;
  overflow-wrap: anywhere;
}

.drawer-scroll {
  flex: 1;
  overflow-y: auto;
}

.task-result {
  flex: none;
}

.duration-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0 26px 16px;
  overflow: hidden;
  border: 1px solid var(--task-line);
  border-radius: 10px;
  background: #171d2a;
}

.duration-summary article {
  min-height: 86px;
  padding: 16px;
  border-right: 1px solid var(--task-line);
}

.duration-summary article:last-child {
  border-right: 0;
}

.duration-summary span {
  display: block;
  color: #8e98a9;
  font-size: 12px;
}

.duration-summary strong {
  display: block;
  margin-top: 9px;
  color: #f0c9ff;
  font-size: 16px;
}

.timeline-card {
  margin: 0 26px 16px;
  padding: 17px;
  border: 1px solid var(--task-line);
  border-radius: 10px;
  background: #171d2a;
}

.drawer-section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.drawer-section-heading h3 {
  margin: 0;
  color: #f0f2f6;
  font-size: 15px;
}

.drawer-section-heading p {
  margin: 5px 0 0;
  color: #8d97a8;
  font-size: 12px;
}

.drawer-section-heading > span {
  color: #bd91ef;
  font-size: 12px;
  white-space: nowrap;
}

.timeline-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 17px 0 0;
  padding: 0;
  list-style: none;
}

.timeline-grid li {
  display: grid;
  grid-template-columns: 9px 1fr;
  gap: 5px 8px;
  padding: 12px;
  border: 1px solid #303748;
  border-radius: 8px;
  background: #121722;
}

.timeline-grid i {
  grid-row: 1 / span 2;
  width: 8px;
  height: 8px;
  margin-top: 4px;
  border-radius: 50%;
  background: #31d5a7;
  box-shadow: 0 0 8px rgb(49 213 167 / 45%);
}

.timeline-grid span {
  color: #8e98aa;
  font-size: 12px;
}

.timeline-grid strong {
  color: #dce1e9;
  font-size: 13px;
  font-weight: 650;
}

.timeline-grid li.missing i {
  background: #596275;
  box-shadow: none;
}

.timeline-grid li.missing strong {
  color: #7b8596;
}

.task-facts {
  flex: none;
  overflow: visible;
  margin: 0 26px;
  padding: 0;
  border-top: 1px solid var(--task-line);
}

.batch-facts {
  max-height: none;
}

.task-facts dt,
.task-facts dd {
  font-size: 13px;
}

.task-facts dd {
  color: #e0e4eb;
}

.batch-items-panel,
.batch-artifacts,
.task-error,
.task-help {
  flex: none;
}

.task-drawer > footer {
  flex: none;
}

@media (max-width: 1120px) {
  .task-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .task-metrics article:nth-child(2) {
    border-right: 0;
  }

  .task-metrics article:nth-child(-n + 2) {
    border-bottom: 1px solid var(--task-line);
  }

  .filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .clear-filter {
    width: fit-content;
  }
}

@media (max-width: 720px) {
  .jobs-hero,
  .task-center-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .jobs-hero h1 {
    font-size: 30px;
  }

  .hero-actions,
  .scope-tabs {
    justify-content: space-between;
  }

  .view-switch {
    overflow-x: auto;
  }

  .view-switch a {
    flex: none;
  }

  .service-filter-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 9px;
    overflow: visible;
  }

  .service-filter-row .filter-label {
    grid-column: 1 / -1;
    margin: 0;
    white-space: nowrap;
  }

  .filter-chip {
    width: 100%;
    min-width: 0;
    padding: 8px 10px;
    white-space: normal;
  }

  .task-metrics,
  .filter-grid,
  .duration-summary,
  .timeline-grid {
    grid-template-columns: 1fr;
  }

  .task-metrics article,
  .task-metrics article:nth-child(2),
  .duration-summary article {
    border-right: 0;
    border-bottom: 1px solid var(--task-line);
  }

  .task-metrics article:last-child,
  .duration-summary article:last-child {
    border-bottom: 0;
  }

  .task-center {
    padding: 14px;
  }

  .filter-grid {
    gap: 14px;
  }

  .jobs-pagination {
    flex-wrap: wrap;
    justify-content: flex-start;
  }

  .duration-summary,
  .timeline-card,
  .task-facts {
    margin-right: 18px;
    margin-left: 18px;
  }
}
</style>
