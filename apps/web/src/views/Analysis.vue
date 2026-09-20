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
let scopeGeneration = 0;
let requestGeneration = 0;

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
  const currentRequest = ++requestGeneration;
  const currentScope = scopeGeneration;
  const requestedKind = clientKind.value;
  loading.value = true;
  error.value = "";
  try {
    const nextJobs = (await api.jobs(
      undefined,
      requestedKind,
      500,
      true,
    )) as TaskJob[];
    if (
      currentRequest !== requestGeneration ||
      currentScope !== scopeGeneration ||
      requestedKind !== clientKind.value
    )
      return;
    jobs.value = nextJobs;
  } catch (cause) {
    if (
      currentRequest !== requestGeneration ||
      currentScope !== scopeGeneration
    )
      return;
    error.value = cause instanceof Error ? cause.message : "性能数据加载失败";
    throw cause;
  } finally {
    if (currentRequest === requestGeneration) loading.value = false;
  }
}

async function changeClientKind(kind: "production" | "test") {
  if (clientKind.value === kind) return;
  scopeGeneration += 1;
  requestGeneration += 1;
  clientKind.value = kind;
  clearFilters();
  await forceRun();
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

const { run, forceRun, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page analysis-page">
    <header class="ops-command-header">
      <div class="ops-heading">
        <div class="ops-kicker">
          <span>GPU CONTROL</span><i></i><b>性能观测</b>
        </div>
        <h1>任务性能洞察</h1>
        <p>
          把排队、GPU
          执行、组装与发布拆成可比较的运行基线，只呈现服务端真实证据。
        </p>
        <nav class="ops-view-switch" aria-label="任务运营视图">
          <router-link to="/jobs"><span>01</span>任务队列</router-link>
          <router-link to="/analysis"><span>02</span>性能洞察</router-link>
          <router-link to="/asset-processing"
            ><span>03</span>资产处理</router-link
          >
        </nav>
      </div>
      <div class="ops-header-tools">
        <div class="scope-tabs" aria-label="分析数据范围">
          <button
            :class="{ active: clientKind === 'production' }"
            @click="changeClientKind('production')"
          >
            生产流量
          </button>
          <button
            :class="{ active: clientKind === 'test' }"
            @click="changeClientKind('test')"
          >
            测试流量
          </button>
        </div>
        <div class="sync-state">
          <i :class="{ spinning: refreshing }"></i>
          <span>
            {{ refreshing ? "正在重算指标" : "自动同步已开启" }}
            <small>
              最近更新
              {{
                lastUpdatedAt?.toLocaleTimeString("zh-CN", {
                  hour12: false,
                }) ?? "等待首次同步"
              }}
            </small>
          </span>
          <button class="sync-button" :disabled="loading" @click="run">
            {{ loading ? "同步中" : "刷新" }}
          </button>
        </div>
      </div>
    </header>

    <div v-if="error" class="ops-alert" role="alert">
      <span>!</span>
      <div>
        <strong>性能数据暂时不可用</strong><small>{{ error }}</small>
      </div>
      <button @click="run">重新连接</button>
    </div>

    <section
      v-if="loading && !jobs.length"
      class="initial-loading"
      aria-live="polite"
    >
      <i></i><span>正在计算任务性能基线…</span>
    </section>

    <template v-else>
      <section class="performance-overview" aria-label="性能摘要">
        <article class="latency-focus">
          <div class="focus-copy">
            <span>端到端中位数</span>
            <strong>{{
              formatDuration(headlineMetrics.medianEndToEnd)
            }}</strong>
            <small>成功任务从创建到最终完成</small>
          </div>
          <div class="focus-p90">
            <span>P90</span>
            <strong>{{ formatDuration(headlineMetrics.p90EndToEnd) }}</strong>
            <small
              >{{ headlineMetrics.timedSamples }} /
              {{ headlineMetrics.samples }} 完整样本</small
            >
          </div>
        </article>
        <article>
          <span>成功样本</span><strong>{{ headlineMetrics.samples }}</strong
          ><small>当前筛选范围</small>
        </article>
        <article>
          <span>排队中位数</span
          ><strong>{{ formatDuration(headlineMetrics.medianQueue) }}</strong
          ><small>P90 {{ formatDuration(headlineMetrics.p90Queue) }}</small>
        </article>
        <article>
          <span>GPU wall 中位数</span
          ><strong>{{ formatDuration(headlineMetrics.medianGpu) }}</strong
          ><small>不含排队与组装</small>
        </article>
        <article class="throughput-card">
          <span>批次中位吞吐</span
          ><strong>{{ formatRate(headlineMetrics.medianThroughput) }}</strong
          ><small>{{ headlineMetrics.throughputSamples }} 个实测样本</small>
        </article>
      </section>

      <section class="analysis-toolbar" aria-label="性能筛选">
        <div class="toolbar-heading">
          <span>OBSERVATION SCOPE</span>
          <strong>{{ filteredJobs.length }} 条匹配任务</strong>
        </div>
        <label class="analysis-search">
          <span>搜索</span>
          <input
            v-model="query"
            type="search"
            placeholder="任务 ID、功能、工作流、API、节点"
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
          <span>API 入口</span>
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
        <button class="reset-filter" @click="clearFilters">重置</button>
      </section>

      <div class="insight-workspace">
        <section class="insight-panel phase-card">
          <header class="panel-heading">
            <div>
              <span>STAGE BASELINE</span>
              <h2>阶段耗时基线</h2>
              <p>对比中位数、P90 与真实字段覆盖。</p>
            </div>
            <b>{{ headlineMetrics.samples }} 个成功任务</b>
          </header>
          <div class="phase-list">
            <article v-for="phase in phaseMetrics" :key="phase.key">
              <div class="phase-name">
                <strong>{{ phase.label }}</strong
                ><code>{{ phase.description }}</code>
              </div>
              <div class="phase-measure">
                <strong>{{ formatDuration(phase.median) }}</strong
                ><small>P90 {{ formatDuration(phase.p90) }}</small>
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

        <aside class="insight-panel diagnostic-card">
          <header class="panel-heading">
            <div>
              <span>OPERATOR NOTES</span>
              <h2>运行洞察</h2>
              <p>从当前样本自动提取可行动信号。</p>
            </div>
          </header>
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
        </aside>
      </div>

      <section class="insight-panel path-card">
        <header class="panel-heading path-heading">
          <div>
            <span>TASK EVIDENCE</span>
            <h2>逐任务关键路径</h2>
            <p>最多展示当前范围前 100 条；进入任务详情可查看完整运行证据。</p>
          </div>
          <b>{{ filteredJobs.length }} 条匹配</b>
        </header>
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
                  <strong>{{ serviceFor(job).label }}</strong
                  ><code>{{ serviceFor(job).api }}</code>
                </td>
                <td>
                  <span>开始 {{ formatDateTime(job.started_at) }}</span
                  ><span>结束 {{ formatDateTime(job.finished_at) }}</span>
                </td>
                <td class="highlight-duration">
                  {{ formatDuration(endToEndDuration(job)) }}
                </td>
                <td>{{ formatDuration(queueDuration(job)) }}</td>
                <td>{{ formatDuration(gpuDuration(job)) }}</td>
                <td>
                  <span>组装 {{ formatDuration(assemblyDuration(job)) }}</span
                  ><span>发布 {{ formatDuration(publishDuration(job)) }}</span>
                </td>
                <td>
                  <span class="coverage-badge"
                    >{{ observedPhaseCount(job) }} /
                    {{ phaseDefinitions.length }}</span
                  >
                </td>
                <td><StatusMark :value="job.status" /></td>
              </tr>
              <tr v-if="!pathRows.length">
                <td colspan="8" class="empty-analysis">
                  <strong>当前筛选没有性能样本</strong
                  ><span>调整业务范围或重置筛选后再试。</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <footer class="method-note">
        <strong>统计方法</strong>
        <span
          >仅计算服务端同时上报起止时间且区间非负的样本；“未上报”不参与中位数和
          P90。P90 使用 nearest-rank，阶段占比为各阶段中位值的相对比例。</span
        >
      </footer>
    </template>
  </div>
</template>

<style scoped>
.analysis-page {
  --analysis-panel: #0c171e;
  --analysis-raised: #102029;
  --analysis-line: rgba(173, 218, 232, 0.14);
  --analysis-muted: #8da2ad;
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
  color: #50cfee;
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
  border-color: rgb(80 207 238 / 28%);
  background: rgb(80 207 238 / 10%);
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
  background: #50cfee;
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
  border-color: #50cfee;
  box-shadow: 0 0 0 3px rgb(80 207 238 / 10%);
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
    rgb(32 184 226 / 15%),
    rgb(80 207 238 / 8%)
  );
}

.analysis-metrics .accent-card strong {
  color: #77def4;
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
  background: linear-gradient(90deg, #289fbe, #50cfee);
}

.phase-list article > b {
  color: #65d8f1;
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
  background: #50cfee;
  box-shadow: 0 0 9px rgb(80 207 238 / 45%);
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
  color: #69daf2 !important;
  font-size: 14px;
  font-weight: 720;
}

.coverage-badge {
  display: inline-flex !important;
  width: fit-content;
  min-height: 28px;
  align-items: center;
  padding: 0 9px;
  color: #94e7f7 !important;
  border: 1px solid rgb(80 207 238 / 28%);
  border-radius: 999px;
  background: rgb(80 207 238 / 9%);
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

/* WebUI 2.0 · performance observatory */
.analysis-page {
  --ops-surface: #0c181e;
  --ops-surface-2: #102129;
  --ops-line: rgb(166 215 226 / 13%);
  --ops-line-strong: rgb(166 215 226 / 23%);
  --ops-text: #eef6f7;
  --ops-muted: #81949a;
  --ops-cyan: #58d4e8;
  --ops-green: #66d7a5;
  --ops-amber: #e7b85b;
  position: relative;
  isolation: isolate;
  padding-bottom: 56px;
}

.analysis-page::before {
  position: absolute;
  z-index: -1;
  top: -42px;
  right: -40px;
  width: 420px;
  height: 280px;
  border-radius: 50%;
  background: radial-gradient(circle, rgb(57 184 205 / 12%), transparent 68%);
  content: "";
  pointer-events: none;
}

.ops-command-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: end;
  gap: 40px;
  margin-bottom: 24px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--ops-line);
}

.ops-heading {
  min-width: 0;
}

.ops-kicker {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 12px;
  color: #779096;
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.ops-kicker i {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--ops-cyan);
}

.ops-kicker b {
  color: var(--ops-cyan);
  font-weight: inherit;
}

.ops-command-header h1 {
  margin: 0;
  color: var(--ops-text);
  font-size: clamp(34px, 3.4vw, 50px);
  font-weight: 680;
  letter-spacing: -0.045em;
  line-height: 1;
}

.ops-command-header p {
  max-width: 720px;
  margin: 13px 0 0;
  color: #90a2a8;
  font-size: 14px;
  line-height: 1.65;
}

.ops-view-switch {
  display: flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  margin-top: 22px;
  padding: 4px;
  border: 1px solid var(--ops-line);
  border-radius: 10px;
  background: rgb(5 14 18 / 68%);
}

.ops-view-switch a {
  display: inline-flex;
  min-height: 38px;
  align-items: center;
  gap: 9px;
  padding: 0 14px;
  color: #82969c;
  border-radius: 7px;
  font-size: 13px;
  font-weight: 650;
  text-decoration: none;
  transition: 160ms ease;
}

.ops-view-switch a span {
  color: #52656b;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
}

.ops-view-switch a:hover {
  color: #cbdadd;
  background: rgb(255 255 255 / 3%);
}

.ops-view-switch a.router-link-active {
  color: #eaf9fa;
  background: #152930;
  box-shadow: inset 0 0 0 1px rgb(88 212 232 / 17%);
}

.ops-view-switch a.router-link-active span {
  color: var(--ops-cyan);
}

.ops-header-tools {
  display: grid;
  justify-items: end;
  gap: 14px;
}

.ops-header-tools .scope-tabs {
  display: flex;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--ops-line);
  border-radius: 9px;
  background: #09151a;
}

.ops-header-tools .scope-tabs button {
  min-height: 34px;
  padding: 0 13px;
  color: #73888e;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 12px;
  font-weight: 680;
  cursor: pointer;
}

.ops-header-tools .scope-tabs button.active {
  color: #dff7f8;
  background: #183039;
}

.sync-state {
  display: flex;
  min-width: 285px;
  align-items: center;
  gap: 11px;
  padding: 10px 10px 10px 13px;
  border: 1px solid var(--ops-line);
  border-radius: 10px;
  background: #0a161b;
}

.sync-state > i {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: var(--ops-green);
  box-shadow: 0 0 0 4px rgb(102 215 165 / 9%);
}

.sync-state > i.spinning {
  background: var(--ops-cyan);
  animation: analysis-pulse 900ms infinite alternate;
}

.sync-state > span {
  display: grid;
  flex: 1;
  gap: 2px;
  color: #c6d5d8;
  font-size: 12px;
  font-weight: 650;
}

.sync-state small {
  color: #62777d;
  font-size: 10px;
  font-weight: 520;
}

.sync-button,
.ops-alert button,
.reset-filter {
  border: 1px solid var(--ops-line-strong);
  border-radius: 7px;
  background: #112229;
  color: #bad1d5;
  font-size: 12px;
  font-weight: 680;
  cursor: pointer;
}

.sync-button {
  min-height: 32px;
  padding: 0 11px;
}
.sync-button:disabled {
  opacity: 0.42;
  cursor: not-allowed;
}

.ops-alert {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 13px;
  margin-bottom: 18px;
  padding: 13px 14px;
  color: #f4d7d5;
  border: 1px solid rgb(238 120 110 / 28%);
  border-radius: 10px;
  background: rgb(95 31 29 / 20%);
}

.ops-alert > span {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  color: #ffaaa2;
  border-radius: 8px;
  background: rgb(238 120 110 / 13%);
  font-weight: 800;
}

.ops-alert div {
  display: grid;
  gap: 3px;
}
.ops-alert strong {
  font-size: 13px;
}
.ops-alert small {
  color: #b8908d;
  font-size: 11px;
}
.ops-alert button {
  min-height: 34px;
  padding: 0 12px;
}

.initial-loading {
  display: flex;
  min-height: 360px;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #84979d;
  border: 1px solid var(--ops-line);
  border-radius: 14px;
  background: linear-gradient(145deg, #0b171c, #0d1c22);
  font-size: 13px;
}

.initial-loading i {
  width: 15px;
  height: 15px;
  border: 2px solid rgb(88 212 232 / 18%);
  border-top-color: var(--ops-cyan);
  border-radius: 50%;
  animation: ops-spin 900ms linear infinite;
}

@keyframes ops-spin {
  to {
    transform: rotate(360deg);
  }
}

.performance-overview {
  display: grid;
  grid-template-columns: minmax(330px, 1.5fr) repeat(4, minmax(140px, 0.72fr));
  gap: 8px;
  margin-bottom: 14px;
}

.performance-overview article {
  display: flex;
  min-height: 116px;
  flex-direction: column;
  justify-content: center;
  padding: 18px 20px;
  border: 1px solid var(--ops-line);
  border-radius: 11px;
  background: linear-gradient(150deg, #0d1a20, #0a151a);
}

.performance-overview article > span,
.performance-overview article small {
  color: var(--ops-muted);
  font-size: 11px;
}

.performance-overview article > strong {
  margin: 9px 0 7px;
  color: var(--ops-text);
  font-size: 21px;
  font-weight: 670;
  letter-spacing: -0.035em;
}

.performance-overview .latency-focus {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 24px;
  background: linear-gradient(135deg, #11262d, #0b171c);
}

.focus-copy,
.focus-p90 {
  display: grid;
  gap: 7px;
}
.focus-copy > span,
.focus-p90 > span {
  color: #8ca0a5;
  font-size: 11px;
}
.focus-copy > strong {
  color: var(--ops-cyan);
  font-size: 33px;
  font-weight: 620;
  letter-spacing: -0.045em;
}
.focus-copy > small,
.focus-p90 > small {
  color: #61767c;
  font-size: 10px;
}

.focus-p90 {
  min-width: 120px;
  padding-left: 20px;
  border-left: 1px solid var(--ops-line);
}

.focus-p90 > strong {
  color: #d9e9eb;
  font-size: 18px;
}
.throughput-card > strong {
  color: #b8eef4 !important;
  font-size: 15px !important;
}

.analysis-toolbar {
  display: grid;
  grid-template-columns:
    145px minmax(230px, 1.3fr) repeat(4, minmax(140px, 0.72fr))
    auto;
  align-items: end;
  gap: 9px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid var(--ops-line);
  border-radius: 11px;
  background: #0a161b;
}

.toolbar-heading {
  display: grid;
  align-self: center;
  gap: 5px;
  padding: 0 7px;
}

.toolbar-heading span,
.panel-heading span {
  color: var(--ops-cyan);
  font-size: 9px;
  font-weight: 760;
  letter-spacing: 0.14em;
}

.toolbar-heading strong {
  color: #cbdadc;
  font-size: 11px;
}

.analysis-toolbar label {
  display: grid;
  min-width: 0;
  gap: 6px;
}

.analysis-toolbar label > span {
  color: #60757b;
  font-size: 9px;
  font-weight: 720;
  letter-spacing: 0.08em;
}

.analysis-toolbar input,
.analysis-toolbar select {
  width: 100%;
  height: 37px;
  min-width: 0;
  padding: 0 11px;
  color: #d8e5e7;
  border: 1px solid var(--ops-line);
  border-radius: 7px;
  outline: none;
  background: #0e1c22;
  font-size: 11px;
}

.analysis-toolbar input:focus,
.analysis-toolbar select:focus {
  border-color: rgb(88 212 232 / 45%);
  box-shadow: 0 0 0 3px rgb(88 212 232 / 7%);
}

.analysis-toolbar .reset-filter {
  height: 37px;
  padding: 0 12px;
}

.insight-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(300px, 0.72fr);
  gap: 14px;
  margin-bottom: 14px;
}

.insight-panel {
  overflow: hidden;
  border: 1px solid var(--ops-line);
  border-radius: 13px;
  background: #0b171c;
  box-shadow: 0 20px 55px rgb(0 0 0 / 13%);
}

.panel-heading {
  display: flex;
  min-height: 86px;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--ops-line);
  background: #0a151a;
}

.panel-heading h2 {
  margin: 6px 0 0;
  color: var(--ops-text);
  font-size: 19px;
  font-weight: 650;
}

.panel-heading p {
  margin: 5px 0 0;
  color: #71868c;
  font-size: 11px;
}
.panel-heading > b {
  color: #778c91;
  font-size: 10px;
  font-weight: 620;
}

.insight-panel .phase-list article {
  grid-template-columns: minmax(175px, 1fr) 100px minmax(120px, 1.2fr) 42px 80px;
  min-height: 82px;
  gap: 13px;
  padding: 14px 20px;
  border-color: var(--ops-line);
  background: #0d1a20;
}

.insight-panel .phase-name strong {
  color: #dce8ea;
  font-size: 13px;
}
.insight-panel .phase-name code {
  color: #65797f;
  font-size: 10px;
}
.insight-panel .phase-measure strong {
  color: #c3f0f4;
  font-size: 13px;
}
.insight-panel .phase-measure small {
  color: #667a80;
  font-size: 10px;
}
.insight-panel .phase-bar {
  height: 6px;
  background: #182a30;
}
.insight-panel .phase-bar i {
  background: linear-gradient(90deg, #337f8f, var(--ops-cyan));
}
.insight-panel .phase-list article > b {
  color: var(--ops-cyan);
  font-size: 11px;
}
.insight-panel .phase-list article > span {
  color: #677b81;
  font-size: 10px;
}

.insight-panel .diagnostic-list article {
  gap: 12px;
  padding: 17px 18px;
  border-color: var(--ops-line);
  background: #0d1a20;
}

.insight-panel .diagnostic-list strong {
  color: #dce8ea;
  font-size: 12px;
}
.insight-panel .diagnostic-list p {
  color: #7d9197;
  font-size: 11px;
  line-height: 1.55;
}
.insight-panel .diagnostic-list .accent i {
  background: var(--ops-cyan);
}
.insight-panel .diagnostic-list .success i {
  background: var(--ops-green);
}
.insight-panel .diagnostic-list .warning i {
  background: var(--ops-amber);
}

.path-card {
  margin-bottom: 14px;
}
.path-table {
  min-width: 1240px;
  font-size: 11px;
}
.path-table th {
  color: #6f8389;
  border-color: var(--ops-line);
  background: #081217;
  font-size: 10px;
}
.path-table td {
  color: #bdcdd0;
  border-color: var(--ops-line);
  background: #0d1a20;
}
.path-table tr:hover td {
  background: #112229;
}
.path-table a {
  color: #d9f5f7;
  font-size: 12px;
}
.path-table td > strong {
  color: #91a6ab;
  font-size: 10px;
}
.path-table td > code,
.path-table td > span {
  color: #687c82;
  font-size: 10px;
}
.highlight-duration {
  color: var(--ops-cyan) !important;
}
.coverage-badge {
  color: #a8e7ee !important;
  border-color: rgb(88 212 232 / 20%);
  background: rgb(88 212 232 / 6%);
}

.empty-analysis {
  height: 220px;
}

.empty-analysis strong,
.empty-analysis span {
  display: block;
}
.empty-analysis strong {
  color: #a5b7bb;
  font-size: 13px;
}
.empty-analysis span {
  margin-top: 7px;
  color: #62767c;
  font-size: 10px;
}

.method-note {
  gap: 14px;
  padding: 14px 16px;
  color: #6e8288;
  border-color: var(--ops-line);
  border-radius: 9px;
  background: #091419;
  font-size: 10px;
}

.method-note strong {
  color: #a9babc;
}

@media (max-width: 1280px) {
  .performance-overview {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .performance-overview .latency-focus {
    grid-column: span 2;
  }
  .performance-overview .throughput-card {
    grid-column: span 2;
  }
  .analysis-toolbar {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .toolbar-heading {
    grid-column: span 3;
  }
  .analysis-toolbar .reset-filter {
    width: fit-content;
  }
}

@media (max-width: 980px) {
  .ops-command-header {
    grid-template-columns: 1fr;
  }
  .ops-header-tools {
    grid-template-columns: 1fr auto;
    align-items: center;
    justify-items: stretch;
  }
  .insight-workspace {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 720px) {
  .ops-command-header h1 {
    font-size: 34px;
  }
  .ops-view-switch {
    width: 100%;
    overflow-x: auto;
  }
  .ops-view-switch a {
    flex: none;
  }
  .ops-header-tools {
    grid-template-columns: 1fr;
    justify-items: stretch;
  }
  .sync-state {
    min-width: 0;
  }
  .ops-alert {
    grid-template-columns: 30px minmax(0, 1fr);
  }
  .ops-alert button {
    grid-column: 1 / -1;
  }
  .performance-overview {
    grid-template-columns: 1fr 1fr;
  }
  .performance-overview .latency-focus,
  .performance-overview .throughput-card {
    grid-column: 1 / -1;
  }
  .performance-overview .latency-focus {
    grid-template-columns: 1fr;
  }
  .focus-p90 {
    padding: 14px 0 0;
    border-top: 1px solid var(--ops-line);
    border-left: 0;
  }
  .analysis-toolbar {
    grid-template-columns: 1fr;
  }
  .toolbar-heading {
    grid-column: auto;
  }
  .insight-panel .phase-list article {
    grid-template-columns: 1fr auto;
  }
  .insight-panel .phase-bar {
    grid-column: 1 / -1;
  }
  .insight-panel .phase-list article > span {
    text-align: left;
  }
}
</style>
