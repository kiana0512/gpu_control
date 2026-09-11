<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { api } from "../api";
import {
  assemblyDuration,
  endToEndDuration,
  formatDateTime,
  formatDuration,
  gpuDuration,
  median,
  percentile,
  publishDuration,
  queueDuration,
  serviceFor,
  statusGroup,
  taskSearchText,
  validationDuration,
  type TaskJob,
} from "../jobPresentation";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";

const jobs = ref<TaskJob[]>([]);
const clientKind = ref<"production" | "test">("production");
const loading = ref(false);
const error = ref("");
const query = ref("");
const serviceFilter = ref("all");
const workflowFilter = ref("all");
const apiFilter = ref("all");
const statusFilter = ref("all");

const serviceOptions = computed(() => {
  const values = new Map<string, { key: string; label: string }>();
  for (const job of jobs.value) {
    const service = serviceFor(job);
    values.set(service.key, { key: service.key, label: service.shortLabel });
  }
  return [...values.values()].sort((left, right) =>
    left.label.localeCompare(right.label, "zh-CN"),
  );
});
const workflowOptions = computed(() =>
  [...new Set(jobs.value.map((job) => job.workflow_key))].sort(),
);
const apiOptions = computed(() =>
  [...new Set(jobs.value.map((job) => serviceFor(job).api))].sort(),
);

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
const successfulJobs = computed(() =>
  filteredJobs.value.filter((job) => job.status === "SUCCEEDED"),
);

const phaseDefinitions = [
  {
    key: "validation",
    label: "创建与校验",
    description: "created_at → validated_at",
    duration: validationDuration,
  },
  {
    key: "queue",
    label: "真实排队",
    description: "queued_at → started_at",
    duration: queueDuration,
  },
  {
    key: "gpu",
    label: "GPU 执行",
    description: "started_at → execution_finished_at",
    duration: gpuDuration,
  },
  {
    key: "assembly",
    label: "结果组装",
    description: "assembling_at → artifact_ready_at",
    duration: assemblyDuration,
  },
  {
    key: "publish",
    label: "产物发布",
    description: "artifact_ready_at → finished_at",
    duration: publishDuration,
  },
] as const;

const phaseMetrics = computed(() => {
  const phases = phaseDefinitions.map((phase) => {
    const values = successfulJobs.value.map(phase.duration);
    const observed = values.filter((value): value is number => value !== null);
    return {
      ...phase,
      observed: observed.length,
      median: median(observed),
      p90: percentile(observed, 0.9),
    };
  });
  const medianTotal = phases.reduce(
    (sum, phase) => sum + (phase.median ?? 0),
    0,
  );
  return phases.map((phase) => ({
    ...phase,
    share:
      phase.median !== null && medianTotal > 0
        ? Math.round((phase.median / medianTotal) * 100)
        : null,
  }));
});

const headlineMetrics = computed(() => {
  const endToEndValues = successfulJobs.value.map(endToEndDuration);
  const queueValues = successfulJobs.value.map(queueDuration);
  const gpuValues = successfulJobs.value.map(gpuDuration);
  const throughputValues = successfulJobs.value.map(
    (job) => job.performance?.frames_per_gpu_minute,
  );
  const timedSamples = endToEndValues.filter(
    (value): value is number => value !== null,
  ).length;
  const throughputSamples = throughputValues.filter(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value) && value >= 0,
  ).length;
  return {
    samples: successfulJobs.value.length,
    timedSamples,
    medianEndToEnd: median(endToEndValues),
    p90EndToEnd: percentile(endToEndValues, 0.9),
    medianQueue: median(queueValues),
    p90Queue: percentile(queueValues, 0.9),
    medianGpu: median(gpuValues),
    medianThroughput: median(throughputValues),
    throughputSamples,
  };
});

const diagnostics = computed(() => {
  const observations: Array<{
    tone: "accent" | "warning" | "neutral" | "success";
    title: string;
    detail: string;
  }> = [];
  const observedPhases = phaseMetrics.value.filter(
    (phase) => phase.median !== null,
  );
  const slowest = [...observedPhases].sort(
    (left, right) => (right.median ?? 0) - (left.median ?? 0),
  )[0];
  if (slowest) {
    observations.push({
      tone: "accent",
      title: `最大已观测阶段：${slowest.label}`,
      detail: `中位耗时 ${formatDuration(slowest.median)}，来自 ${slowest.observed} 个字段完整的成功任务。`,
    });
  } else {
    observations.push({
      tone: "neutral",
      title: "尚不能定位耗时阶段",
      detail: "当前筛选范围没有任何同时具备阶段起止时间的成功任务。",
    });
  }

  const missingEndToEnd =
    headlineMetrics.value.samples - headlineMetrics.value.timedSamples;
  if (missingEndToEnd > 0) {
    observations.push({
      tone: "warning",
      title: `${missingEndToEnd} 个成功任务缺少完整端到端时间`,
      detail:
        "这些任务不进入端到端中位数和 P90；界面不会用最后进度或当前时间代替 finished_at。",
    });
  } else if (headlineMetrics.value.samples > 0) {
    observations.push({
      tone: "success",
      title: "端到端时间覆盖完整",
      detail: `${headlineMetrics.value.samples} 个成功任务均上报 created_at 与 finished_at。`,
    });
  }

  const weakestCoverage = [...phaseMetrics.value].sort(
    (left, right) => left.observed - right.observed,
  )[0];
  if (
    weakestCoverage &&
    weakestCoverage.observed < headlineMetrics.value.samples
  ) {
    observations.push({
      tone: "neutral",
      title: `${weakestCoverage.label}字段覆盖 ${weakestCoverage.observed} / ${headlineMetrics.value.samples}`,
      detail: `缺少 ${weakestCoverage.description} 的任务不计入该阶段统计。`,
    });
  }

  if (headlineMetrics.value.p90Queue !== null) {
    observations.push({
      tone: "neutral",
      title: `真实排队 P90：${formatDuration(headlineMetrics.value.p90Queue)}`,
      detail: "只使用 queued_at → started_at 或服务端 performance.queue_ms。",
    });
  }
  return observations;
});

const pathRows = computed(() => filteredJobs.value.slice(0, 100));

async function load() {
  loading.value = true;
  error.value = "";
  try {
    jobs.value = (await api.jobs(undefined, clientKind.value)) as TaskJob[];
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "性能数据加载失败";
    throw cause;
  } finally {
    loading.value = false;
  }
}

async function changeClientKind(kind: "production" | "test") {
  if (clientKind.value === kind) return;
  clientKind.value = kind;
  clearFilters();
  await run();
}

function clearFilters() {
  query.value = "";
  serviceFilter.value = "all";
  workflowFilter.value = "all";
  apiFilter.value = "all";
  statusFilter.value = "all";
}

function observedPhaseCount(job: TaskJob) {
  return phaseDefinitions.filter((phase) => phase.duration(job) !== null)
    .length;
}

function formatRate(value: number | null) {
  return value === null ? "未上报" : `${value.toFixed(2)} 帧/分钟`;
}

watch([serviceFilter, workflowFilter, apiFilter], () => {
  // Filters remain intentionally independent so operators can intersect API,
  // workflow and business-function dimensions without hidden resets.
});

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page analysis-page">
    <header class="analysis-hero">
      <div>
        <span class="hero-eyebrow">PERFORMANCE · REPORTED DATA ONLY</span>
        <h1>任务性能分析</h1>
        <p>
          用同一套任务分类拆解真实排队、GPU wall
          time、结果组装与发布；缺失字段不参与统计。
        </p>
        <nav class="view-switch" aria-label="任务与分析视图">
          <router-link to="/jobs">任务中心</router-link>
          <router-link to="/analysis">性能分析</router-link>
        </nav>
      </div>
      <div class="analysis-actions">
        <div class="scope-tabs" aria-label="分析数据范围">
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
      <strong>分析数据同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="analysis-filter-panel">
      <label class="search-control">
        <span>搜索任务</span>
        <input
          v-model="query"
          type="search"
          placeholder="任务 ID、功能、工作流、API、节点…"
        />
      </label>
      <label>
        <span>业务功能</span>
        <select v-model="serviceFilter">
          <option value="all">全部功能</option>
          <option
            v-for="service in serviceOptions"
            :key="service.key"
            :value="service.key"
          >
            {{ service.label }}
          </option>
        </select>
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
          <option value="all">全部状态</option>
          <option value="active">正在处理</option>
          <option value="queued">排队 / 校验</option>
          <option value="succeeded">已完成</option>
          <option value="attention">异常 / 取消</option>
        </select>
      </label>
      <button class="clear-filter" @click="clearFilters">清除筛选</button>
    </section>

    <section class="analysis-metrics" aria-label="性能摘要">
      <article>
        <span>成功样本</span>
        <strong>{{ headlineMetrics.samples }}</strong>
        <small>{{ headlineMetrics.timedSamples }} 个端到端时间完整</small>
      </article>
      <article>
        <span>端到端中位数</span>
        <strong>{{ formatDuration(headlineMetrics.medianEndToEnd) }}</strong>
        <small>P90 {{ formatDuration(headlineMetrics.p90EndToEnd) }}</small>
      </article>
      <article>
        <span>真实排队中位数</span>
        <strong>{{ formatDuration(headlineMetrics.medianQueue) }}</strong>
        <small>P90 {{ formatDuration(headlineMetrics.p90Queue) }}</small>
      </article>
      <article>
        <span>GPU wall 中位数</span>
        <strong>{{ formatDuration(headlineMetrics.medianGpu) }}</strong>
        <small>不包含排队与结果组装</small>
      </article>
      <article class="accent-card">
        <span>批次中位吞吐</span>
        <strong>{{ formatRate(headlineMetrics.medianThroughput) }}</strong>
        <small>{{ headlineMetrics.throughputSamples }} 个服务端实测样本</small>
      </article>
    </section>

    <div class="analysis-grid">
      <section class="analysis-card phase-card">
        <div class="card-heading">
          <div>
            <span class="section-eyebrow">STAGE BASELINE</span>
            <h2>阶段耗时基线</h2>
            <p>成功任务中位数、P90 与字段覆盖率。</p>
          </div>
          <span>{{ headlineMetrics.samples }} 个成功任务</span>
        </div>
        <div class="phase-list">
          <article v-for="phase in phaseMetrics" :key="phase.key">
            <div class="phase-name">
              <strong>{{ phase.label }}</strong>
              <code>{{ phase.description }}</code>
            </div>
            <div class="phase-measure">
              <strong>{{ formatDuration(phase.median) }}</strong>
              <small>P90 {{ formatDuration(phase.p90) }}</small>
            </div>
            <div class="phase-bar">
              <i :style="{ width: `${phase.share ?? 0}%` }"></i>
            </div>
            <b>{{ phase.share === null ? "—" : `${phase.share}%` }}</b>
            <span
              >{{ phase.observed }} / {{ headlineMetrics.samples }} 实测</span
            >
          </article>
        </div>
      </section>

      <section class="analysis-card diagnostic-card">
        <div class="card-heading">
          <div>
            <span class="section-eyebrow">DIAGNOSTICS</span>
            <h2>速度诊断</h2>
            <p>所有结论同时显示样本数与取值边界。</p>
          </div>
        </div>
        <div class="diagnostic-list">
          <article
            v-for="item in diagnostics"
            :key="item.title"
            :class="item.tone"
          >
            <i></i>
            <div>
              <strong>{{ item.title }}</strong>
              <p>{{ item.detail }}</p>
            </div>
          </article>
        </div>
      </section>
    </div>

    <section class="analysis-card path-card">
      <div class="card-heading path-heading">
        <div>
          <span class="section-eyebrow">TASK PATH</span>
          <h2>逐任务关键路径</h2>
          <p>最多展示当前筛选的前 100 条；点任务名进入完整详情。</p>
        </div>
        <span>{{ filteredJobs.length }} 条匹配</span>
      </div>
      <div class="path-table-wrap">
        <table class="path-table">
          <thead>
            <tr>
              <th>任务 / 功能</th>
              <th>开始 / 结束</th>
              <th>端到端</th>
              <th>真实排队</th>
              <th>GPU wall</th>
              <th>组装 / 发布</th>
              <th>阶段覆盖</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="job in pathRows" :key="job.job_id">
              <td>
                <router-link
                  :to="{
                    path: '/jobs',
                    query: { job: job.job_id, kind: clientKind },
                  }"
                >
                  {{ job.external_batch_id || job.job_id }}
                </router-link>
                <strong>{{ serviceFor(job).label }}</strong>
                <code>{{ serviceFor(job).api }}</code>
              </td>
              <td>
                <span>开始 {{ formatDateTime(job.started_at) }}</span>
                <span>结束 {{ formatDateTime(job.finished_at) }}</span>
              </td>
              <td class="highlight-duration">
                {{ formatDuration(endToEndDuration(job)) }}
              </td>
              <td>{{ formatDuration(queueDuration(job)) }}</td>
              <td>{{ formatDuration(gpuDuration(job)) }}</td>
              <td>
                <span>组装 {{ formatDuration(assemblyDuration(job)) }}</span>
                <span>发布 {{ formatDuration(publishDuration(job)) }}</span>
              </td>
              <td>
                <span class="coverage-badge">
                  {{ observedPhaseCount(job) }} / {{ phaseDefinitions.length }}
                </span>
              </td>
              <td><StatusMark :value="job.status" /></td>
            </tr>
            <tr v-if="!pathRows.length">
              <td colspan="8" class="empty-analysis">
                当前筛选没有可展示的任务。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <footer class="method-note">
      <strong>统计口径</strong>
      <span>
        仅计算服务端已同时上报起止时间且区间非负的样本；中位数与 P90
        不包含“未上报”；P90 使用
        nearest-rank。阶段百分比是各阶段中位值的相对比例，不代表同一任务的耗时拆分。
      </span>
    </footer>
  </div>
</template>

<style scoped>
.analysis-page {
  --analysis-panel: #11141f;
  --analysis-raised: #171a27;
  --analysis-line: #2b3040;
  --analysis-muted: #929bad;
  padding-bottom: 52px;
}

.analysis-hero {
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

.analysis-hero h1 {
  margin: 0;
  color: #fbfaff;
  font-size: clamp(30px, 3vw, 42px);
  font-weight: 780;
  letter-spacing: -0.035em;
  line-height: 1.08;
}

.analysis-hero p {
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

.analysis-actions {
  display: flex;
  align-items: center;
  gap: 12px;
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
  animation: analysis-pulse 900ms infinite alternate;
}

.refresh-copy small {
  color: #7e8798;
  font-size: 12px;
  font-weight: 500;
}

@keyframes analysis-pulse {
  to {
    opacity: 0.35;
  }
}

.analysis-filter-panel {
  display: grid;
  grid-template-columns:
    minmax(250px, 1.3fr) repeat(4, minmax(155px, 0.8fr))
    auto;
  align-items: end;
  gap: 12px;
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid var(--analysis-line);
  border-radius: 12px;
  background: var(--analysis-panel);
}

.analysis-filter-panel label {
  display: grid;
  gap: 7px;
  min-width: 0;
}

.analysis-filter-panel label > span {
  color: #9099aa;
  font-size: 12px;
  font-weight: 650;
}

.analysis-filter-panel input,
.analysis-filter-panel select {
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

.analysis-filter-panel input:focus,
.analysis-filter-panel select:focus {
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

.analysis-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  overflow: hidden;
  margin-bottom: 16px;
  border: 1px solid var(--analysis-line);
  border-radius: 13px;
  background: var(--analysis-panel);
}

.analysis-metrics article {
  min-height: 116px;
  padding: 20px 22px;
  border-right: 1px solid var(--analysis-line);
}

.analysis-metrics article:last-child {
  border-right: 0;
}

.analysis-metrics span,
.analysis-metrics small {
  display: block;
  color: var(--analysis-muted);
  font-size: 12px;
}

.analysis-metrics strong {
  display: block;
  margin: 11px 0 8px;
  color: #f4f5f8;
  font-size: 24px;
  line-height: 1;
}

.analysis-metrics .accent-card {
  background: linear-gradient(
    135deg,
    rgb(156 65 223 / 15%),
    rgb(223 76 178 / 8%)
  );
}

.analysis-metrics .accent-card strong {
  color: #f36cc3;
}

.analysis-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(330px, 0.9fr);
  gap: 16px;
  margin-bottom: 16px;
}

.analysis-card {
  overflow: hidden;
  border: 1px solid var(--analysis-line);
  border-radius: 13px;
  background: var(--analysis-panel);
}

.card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 20px 22px;
  border-bottom: 1px solid var(--analysis-line);
}

.card-heading h2 {
  margin: 0;
  color: #f2f3f7;
  font-size: 19px;
}

.card-heading p {
  margin: 6px 0 0;
  color: #8f98a9;
  font-size: 13px;
}

.card-heading > span {
  color: #9aa3b3;
  font-size: 12px;
  white-space: nowrap;
}

.phase-list article {
  display: grid;
  grid-template-columns: minmax(210px, 1fr) 110px minmax(130px, 1.4fr) 48px 94px;
  align-items: center;
  gap: 16px;
  min-height: 88px;
  padding: 15px 22px;
  border-bottom: 1px solid #292e3c;
}

.phase-list article:last-child {
  border-bottom: 0;
}

.phase-name strong,
.phase-name code,
.phase-measure strong,
.phase-measure small {
  display: block;
}

.phase-name strong {
  color: #e9ebf0;
  font-size: 14px;
}

.phase-name code {
  margin-top: 6px;
  color: #828c9e;
  font-size: 12px;
}

.phase-measure strong {
  color: #f0c9ff;
  font-size: 14px;
}

.phase-measure small {
  margin-top: 5px;
  color: #858fa1;
  font-size: 12px;
}

.phase-bar {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: #2b3040;
}

.phase-bar i {
  display: block;
  height: 100%;
  min-width: 0;
  border-radius: inherit;
  background: linear-gradient(90deg, #9f4ff1, #e84eb1);
}

.phase-list article > b {
  color: #e467bf;
  font-size: 13px;
  text-align: right;
}

.phase-list article > span {
  color: #8d96a8;
  font-size: 12px;
  text-align: right;
}

.diagnostic-list article {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr);
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid #292e3c;
}

.diagnostic-list article:last-child {
  border-bottom: 0;
}

.diagnostic-list i {
  width: 9px;
  height: 9px;
  margin-top: 5px;
  border-radius: 50%;
  background: #788295;
}

.diagnostic-list .accent i {
  background: #e44eb3;
  box-shadow: 0 0 9px rgb(228 78 179 / 45%);
}

.diagnostic-list .warning i {
  background: #ffb547;
}

.diagnostic-list .success i {
  background: #2ad6a5;
}

.diagnostic-list strong {
  color: #e8eaf0;
  font-size: 14px;
}

.diagnostic-list p {
  margin: 7px 0 0;
  color: #929bad;
  font-size: 13px;
  line-height: 1.6;
}

.path-card {
  margin-bottom: 16px;
}

.path-table-wrap {
  overflow-x: auto;
}

.path-table {
  min-width: 1320px;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 13px;
}

.path-table th {
  height: 46px;
  padding: 0 16px;
  color: #8e98a9;
  border-bottom: 1px solid var(--analysis-line);
  background: #0d1019;
  font-size: 12px;
  font-weight: 650;
  text-align: left;
}

.path-table th:first-child {
  width: 300px;
}

.path-table th:nth-child(2) {
  width: 210px;
}

.path-table th:nth-child(6) {
  width: 160px;
}

.path-table td {
  min-height: 80px;
  padding: 15px 16px;
  color: #cbd1dc;
  border-bottom: 1px solid #292e3c;
  background: #121620;
  vertical-align: top;
}

.path-table tr:last-child td {
  border-bottom: 0;
}

.path-table tr:hover td {
  background: #171a27;
}

.path-table a,
.path-table td > strong,
.path-table td > code,
.path-table td > span {
  display: block;
}

.path-table a {
  max-width: 270px;
  overflow: hidden;
  color: #efb5ff;
  font-size: 14px;
  font-weight: 720;
  text-decoration: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-table td > strong {
  margin-top: 6px;
  color: #bbc3d0;
  font-size: 12px;
}

.path-table td > code {
  max-width: 270px;
  overflow: hidden;
  margin-top: 5px;
  color: #7f899b;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-table td > span {
  margin-bottom: 6px;
  color: #a9b1bf;
  white-space: nowrap;
}

.highlight-duration {
  color: #ed68bf !important;
  font-size: 14px;
  font-weight: 720;
}

.coverage-badge {
  display: inline-flex !important;
  width: fit-content;
  min-height: 28px;
  align-items: center;
  padding: 0 9px;
  color: #c6a5ee !important;
  border: 1px solid rgb(166 92 237 / 28%);
  border-radius: 999px;
  background: rgb(166 92 237 / 9%);
  font-weight: 700;
}

.empty-analysis {
  height: 180px;
  color: #8f98a9 !important;
  text-align: center;
  vertical-align: middle !important;
}

.method-note {
  display: flex;
  gap: 12px;
  padding: 14px 17px;
  color: #8f98aa;
  border: 1px solid #293040;
  border-radius: 10px;
  background: #0f131d;
  font-size: 12px;
  line-height: 1.6;
}

.method-note strong {
  flex: none;
  color: #c8ced8;
}

@media (max-width: 1250px) {
  .analysis-actions {
    align-items: flex-end;
    flex-direction: column-reverse;
  }

  .analysis-filter-panel {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .analysis-metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .analysis-metrics article:nth-child(3) {
    border-right: 0;
  }

  .analysis-metrics article:nth-child(-n + 3) {
    border-bottom: 1px solid var(--analysis-line);
  }

  .analysis-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .analysis-hero {
    align-items: stretch;
    flex-direction: column;
  }

  .analysis-hero h1 {
    font-size: 30px;
  }

  .analysis-actions {
    align-items: stretch;
  }

  .view-switch {
    overflow-x: auto;
  }

  .analysis-filter-panel,
  .analysis-metrics {
    grid-template-columns: 1fr;
  }

  .analysis-metrics article,
  .analysis-metrics article:nth-child(3) {
    border-right: 0;
    border-bottom: 1px solid var(--analysis-line);
  }

  .analysis-metrics article:last-child {
    border-bottom: 0;
  }

  .phase-list article {
    grid-template-columns: 1fr auto;
  }

  .phase-bar {
    grid-column: 1 / -1;
  }

  .phase-list article > span {
    text-align: left;
  }

  .method-note {
    flex-direction: column;
  }
}
</style>
