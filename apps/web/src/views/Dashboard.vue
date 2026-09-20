<script setup lang="ts">
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { init, use, type ECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import type { AssetProcessingOverview, JobInfo } from "../types";

import { api } from "../api";
import JobsTable from "../components/JobsTable.vue";
import NodeTable from "../components/NodeTable.vue";
import { useSystemStore } from "../stores/system";
import { useAutoRefresh } from "../composables/useAutoRefresh";

import { compareNodes } from "../nodePresentation";

const store = useSystemStore();
const router = useRouter();
const assetOverview = ref<AssetProcessingOverview | null>(null);
const assetError = ref("");
use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

let chart: ECharts | undefined;

const metrics = computed(() => [
  {
    label: "排队任务",
    value: store.dashboard?.jobs.QUEUED ?? 0,
    tone: "accent",
    hint: "等待调度器领取",
  },
  {
    label: "运行中",
    value: store.dashboard?.jobs.RUNNING ?? 0,
    tone: "green",
    hint: "正在 GPU / 汇总阶段",
  },
  {
    label: "今日成功",
    value: store.dashboard?.jobs.SUCCEEDED ?? 0,
    tone: "green",
    hint: "自然日内完成",
  },
  {
    label: "今日失败",
    value: store.dashboard?.jobs.FAILED ?? 0,
    tone: "red",
    hint: "需要查看错误证据",
  },
  {
    label: "最老等待",
    value: formatCompactDuration(store.dashboard?.oldest_wait_seconds ?? 0),
    tone: "amber",
    hint: "当前队列最长等待",
  },
]);
const connectedNodes = computed(() => [...store.nodes].sort(compareNodes));
const assetActive = computed(() => {
  const counts = assetOverview.value?.summary.counts ?? {};
  return (counts.QUEUED ?? 0) + (counts.CLAIMED ?? 0) + (counts.RUNNING ?? 0);
});
const activeGpuNodes = computed(() =>
  connectedNodes.value.filter(
    (node) => node.health === "ONLINE" && node.mode === "ACTIVE",
  ),
);
const activeGpuSlots = computed(() =>
  activeGpuNodes.value.reduce((sum, node) => sum + node.max_concurrency, 0),
);
const usedGpuSlots = computed(() =>
  activeGpuNodes.value.reduce((sum, node) => sum + node.current_jobs, 0),
);
const freeGpuSlots = computed(() =>
  Math.max(0, activeGpuSlots.value - usedGpuSlots.value),
);
const capacityPercent = computed(() =>
  activeGpuSlots.value
    ? Math.min(
        100,
        Math.round((usedGpuSlots.value / activeGpuSlots.value) * 100),
      )
    : 0,
);
const capacityStyle = computed<Record<string, string>>(() => ({
  "--capacity": capacityPercent.value + "%",
}));
const clusterTone = computed(() => {
  if (!connectedNodes.value.length || !activeGpuNodes.value.length)
    return "danger";
  if (activeGpuNodes.value.length < connectedNodes.value.length)
    return "warning";
  return "healthy";
});
const clusterHeadline = computed(() => {
  if (!connectedNodes.value.length) return "等待 GPU 节点心跳";
  if (!activeGpuNodes.value.length) return "节点在线，但当前没有接单槽位";
  if (activeGpuNodes.value.length < connectedNodes.value.length)
    return `${activeGpuNodes.value.length} / ${connectedNodes.value.length} 节点参与接单`;
  return "GPU 集群运行正常";
});
const queueClearText = computed(() => {
  const seconds = store.dashboard?.estimated_clear_seconds;
  return seconds == null ? "样本不足" : formatCompactDuration(seconds);
});
const trendTotal = computed(() =>
  (store.dashboard?.submission_trend ?? []).reduce(
    (sum, point) => sum + point.value,
    0,
  ),
);

function formatCompactDuration(value: number) {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours} 小时${minutes ? ` ${minutes} 分` : ""}`;
}

function resizeChart() {
  chart?.resize();
}

function renderChart() {
  const element = document.getElementById("queue-chart");
  if (!element) return;
  chart ??= init(element);
  chart.setOption(
    {
      animationDuration: 420,
      grid: { left: 44, right: 22, top: 28, bottom: 34 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#0c1920",
        borderColor: "rgba(173,218,232,.24)",
        textStyle: { color: "#f3f8fa", fontSize: 13 },
        formatter: (params: unknown) => {
          const point = (
            params as Array<{ axisValue: string; value: number }>
          )[0];
          return point
            ? `${point.axisValue}<br/><b>${point.value}</b> 个任务`
            : "";
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: store.dashboard?.submission_trend.map((item) => item.label) ?? [],
        axisLabel: { color: "#7f959f", fontSize: 12 },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "rgba(173,218,232,.14)" } },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: "#7f959f", fontSize: 12 },
        splitLine: {
          lineStyle: { type: "dashed", color: "rgba(173,218,232,.1)" },
        },
      },
      series: [
        {
          name: "排队",
          type: "line",
          smooth: 0.32,
          data:
            store.dashboard?.submission_trend.map((item) => item.value) ?? [],
          lineStyle: { color: "#50cfee", width: 3 },
          showSymbol: false,
          areaStyle: { color: "rgba(80,207,238,.1)" },
        },
      ],
    },
    true,
  );
}

async function refresh() {
  const [, assetResult] = await Promise.allSettled([
    store.refresh(store.clientKind),
    api.assetProcessing(),
  ]);
  if (assetResult.status === "fulfilled") {
    assetOverview.value = assetResult.value;
    assetError.value = "";
  } else {
    assetError.value =
      assetResult.reason instanceof Error
        ? assetResult.reason.message
        : "CPU 资产平面状态加载失败";
  }
  renderChart();
  if (store.error) throw new Error(store.error);
}

async function changeClientKind(kind: "production" | "test") {
  if (store.clientKind === kind) return;
  await store.refresh(kind);
  renderChart();
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(refresh);
function openJob(job: JobInfo) {
  void router.push({
    path: "/jobs",
    query: { job: job.job_id, kind: store.clientKind },
  });
}

onMounted(() => {
  window.addEventListener("resize", resizeChart);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
</script>
<template>
  <div class="page dashboard-control-page">
    <header class="ops-hero">
      <div class="ops-hero-copy">
        <span class="ops-kicker">LIVE OPERATIONS / 实时态势</span>
        <h1>{{ clusterHeadline }}</h1>
        <p>
          从这里判断系统是否能接单、任务流向哪里以及哪里需要处置；页面只读取运行态，
          不会改变正在执行的任务。
        </p>
      </div>
      <div class="ops-hero-controls">
        <div class="ops-scope" aria-label="任务数据范围">
          <button
            :class="{ active: store.clientKind === 'production' }"
            @click="changeClientKind('production')"
          >
            生产流量
          </button>
          <button
            :class="{ active: store.clientKind === 'test' }"
            @click="changeClientKind('test')"
          >
            测试流量
          </button>
        </div>
        <button class="ops-refresh" type="button" @click="run">
          <i :class="{ spinning: refreshing }"></i>
          <span>
            <b>{{ refreshing ? "正在同步" : "同步运行态" }}</b>
            <small>{{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small>
          </span>
        </button>
      </div>
    </header>

    <div v-if="store.error" class="ops-error">
      <span>控制面读取异常</span>
      <strong>{{ store.error }}</strong>
      <button @click="refresh">重新连接</button>
    </div>

    <section class="situation-board" :class="clusterTone">
      <div class="situation-primary">
        <div class="situation-label"><i></i>集群实时判断</div>
        <div class="capacity-orbit" :style="capacityStyle">
          <div>
            <strong>{{ capacityPercent }}</strong
            ><span>%</span>
            <small>槽位占用</small>
          </div>
        </div>
        <div class="situation-message">
          <span>{{ activeGpuNodes.length }} 台可调度节点</span>
          <h2>{{ freeGpuSlots }} 个空闲执行槽位</h2>
          <p>
            仅在线、ACTIVE 且兼容当前工作流的节点会收到新任务。预计队列清空：
            <b>{{ queueClearText }}</b>
          </p>
        </div>
      </div>
      <div class="signal-rail" aria-label="关键运行指标">
        <article v-for="metric in metrics" :key="metric.label">
          <span><i :class="metric.tone"></i>{{ metric.label }}</span>
          <strong>{{ metric.value }}</strong>
          <small>{{ metric.hint }}</small>
        </article>
      </div>
    </section>

    <nav class="operation-routes" aria-label="核心操作入口">
      <router-link to="/jobs">
        <span>01</span>
        <div>
          <strong>任务运营</strong>
          <small>{{ store.jobs.length }} 个最近任务</small>
        </div>
        <b>GPU 推理与队列处置 →</b>
      </router-link>
      <router-link to="/cloud-servers">
        <span>02</span>
        <div>
          <strong>云算力</strong>
          <small>AutoDL 电源与接入</small>
        </div>
        <b>管理云 GPU 实例 →</b>
      </router-link>
      <router-link to="/asset-processing" :class="{ degraded: assetError }">
        <span>03</span>
        <div>
          <strong>资产流水线</strong>
          <small v-if="!assetError">
            {{ assetOverview?.summary.online_workers ?? 0 }} Worker ·
            {{ assetActive }} 处理中
          </small>
          <small v-else>{{ assetError }}</small>
        </div>
        <b>UV / 拓扑 / 烘焙 →</b>
      </router-link>
    </nav>

    <section class="compute-fabric">
      <header class="ops-section-heading">
        <div>
          <span>COMPUTE FABRIC</span>
          <h2>算力网络</h2>
          <p>物理节点、云节点与执行槽位的统一状态。</p>
        </div>
        <router-link to="/nodes">管理全部节点 →</router-link>
      </header>
      <NodeTable :nodes="connectedNodes" />
    </section>

    <div class="telemetry-layout">
      <section class="telemetry-panel">
        <header class="ops-section-heading compact">
          <div>
            <span>TRAFFIC</span>
            <h2>任务流入</h2>
            <p>过去 6 小时共提交 {{ trendTotal }} 个任务</p>
          </div>
          <b>每小时</b>
        </header>
        <div id="queue-chart"></div>
      </section>
      <aside class="attention-panel">
        <header class="ops-section-heading compact">
          <div>
            <span>ATTENTION</span>
            <h2>需要处置</h2>
            <p>当前活动告警</p>
          </div>
          <router-link to="/alerts">全部 →</router-link>
        </header>
        <div
          v-if="!store.dashboard?.active_alerts.length"
          class="attention-clear"
        >
          <i></i>
          <strong>运行态稳定</strong>
          <span>当前没有活动告警</span>
        </div>
        <ul v-else class="attention-list">
          <li
            v-for="alert in store.dashboard.active_alerts.slice(0, 3)"
            :key="alert.id"
          >
            <span>{{ alert.severity }}</span>
            <strong>{{ alert.name }}</strong>
            <p>{{ alert.summary }}</p>
          </li>
        </ul>
      </aside>
    </div>

    <section class="activity-ledger">
      <header class="ops-section-heading">
        <div>
          <span>ACTIVITY LEDGER</span>
          <h2>最近任务</h2>
          <p>选择任务可进入完整执行证据与阶段耗时。</p>
        </div>
        <router-link to="/jobs">进入任务运营 →</router-link>
      </header>
      <JobsTable :jobs="store.jobs.slice(0, 8)" @select="openJob" />
    </section>
  </div>
</template>

<style scoped>
.dashboard-control-page {
  --ops-line: rgba(157, 210, 225, 0.14);
  display: grid;
  gap: 22px;
}

.ops-hero {
  min-height: 132px;
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 40px;
  padding: 8px 0 26px;
  border-bottom: 1px solid var(--ops-line);
}

.ops-hero-copy {
  max-width: 850px;
}

.ops-kicker,
.ops-section-heading span {
  display: block;
  margin-bottom: 10px;
  color: #50cfee;
  font-size: 9px;
  font-weight: 820;
  letter-spacing: 0.2em;
}

.ops-hero h1 {
  margin: 0;
  color: #f4fafc;
  font-size: clamp(31px, 3.4vw, 54px);
  font-weight: 790;
  letter-spacing: -0.055em;
  line-height: 1.02;
}

.ops-hero p,
.ops-section-heading p {
  margin: 12px 0 0;
  color: #76909b;
  font-size: 12px;
  line-height: 1.7;
}

.ops-hero-controls,
.ops-scope,
.ops-refresh {
  display: flex;
  align-items: center;
}

.ops-hero-controls {
  gap: 12px;
}

.ops-scope {
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--ops-line);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.018);
}

.ops-scope button {
  height: 34px;
  padding: 0 13px;
  color: #708a95;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
}

.ops-scope button.active {
  color: #e9faff;
  background: rgba(80, 207, 238, 0.12);
}

.ops-refresh {
  height: 42px;
  gap: 9px;
  padding: 0 13px;
  color: #9bb0b8;
  border: 1px solid var(--ops-line);
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.018);
  cursor: pointer;
}

.ops-refresh i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4fd5a4;
  box-shadow: 0 0 11px rgba(79, 213, 164, 0.64);
}

.ops-refresh i.spinning {
  animation: ops-pulse 0.8s ease infinite alternate;
}

.ops-refresh span {
  display: grid;
  gap: 2px;
  text-align: left;
}

.ops-refresh b {
  color: #c8d7dc;
  font-size: 10px;
}

.ops-refresh small {
  color: #5d7681;
  font-size: 8px;
}

.ops-error {
  min-height: 48px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 16px;
  padding: 8px 14px;
  color: #ff9ba4;
  border: 1px solid rgba(255, 116, 130, 0.26);
  border-radius: 7px;
  background: rgba(255, 116, 130, 0.06);
}

.ops-error span {
  font-size: 9px;
  font-weight: 820;
  letter-spacing: 0.12em;
}

.ops-error strong {
  font-size: 11px;
  font-weight: 520;
}

.ops-error button {
  color: inherit;
  border: 0;
  background: transparent;
  cursor: pointer;
}

.situation-board {
  display: grid;
  grid-template-columns: minmax(430px, 1.45fr) minmax(520px, 1fr);
  overflow: hidden;
  border: 1px solid var(--ops-line);
  border-left: 3px solid #4fd5a4;
  border-radius: 9px;
  background: linear-gradient(
    120deg,
    rgba(80, 207, 238, 0.045),
    rgba(9, 22, 29, 0.8) 42%
  );
}

.situation-board.warning {
  border-left-color: #efbd6d;
}

.situation-board.danger {
  border-left-color: #ff7482;
}

.situation-primary {
  position: relative;
  min-height: 230px;
  display: grid;
  grid-template-columns: 142px minmax(0, 1fr);
  align-items: center;
  gap: 28px;
  padding: 48px 34px 28px;
  border-right: 1px solid var(--ops-line);
}

.situation-label {
  position: absolute;
  top: 22px;
  left: 30px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #75919c;
  font-size: 9px;
  font-weight: 760;
  letter-spacing: 0.13em;
}

.situation-label i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4fd5a4;
  box-shadow: 0 0 12px rgba(79, 213, 164, 0.6);
}

.capacity-orbit {
  position: relative;
  width: 132px;
  height: 132px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: conic-gradient(
    #50cfee var(--capacity),
    rgba(156, 211, 226, 0.09) 0
  );
}

.capacity-orbit::before {
  width: 112px;
  height: 112px;
  border-radius: 50%;
  background: #0a171e;
  content: "";
}

.capacity-orbit > div {
  position: absolute;
  display: grid;
  grid-template-columns: auto auto;
  align-items: baseline;
  justify-content: center;
}

.capacity-orbit strong {
  color: #f1fbfd;
  font-size: 34px;
  letter-spacing: -0.06em;
}

.capacity-orbit span {
  color: #50cfee;
  font-size: 11px;
}

.capacity-orbit small {
  grid-column: 1 / -1;
  margin-top: 2px;
  color: #617c87;
  font-size: 8px;
  text-align: center;
}

.situation-message > span {
  color: #50cfee;
  font-size: 10px;
  font-weight: 720;
}

.situation-message h2 {
  margin: 9px 0;
  color: #edf8fa;
  font-size: clamp(23px, 2vw, 32px);
  font-weight: 750;
  letter-spacing: -0.04em;
}

.situation-message p {
  max-width: 510px;
  margin: 0;
  color: #748d97;
  font-size: 11px;
  line-height: 1.65;
}

.situation-message p b {
  color: #aec1c8;
}

.signal-rail {
  display: grid;
  grid-template-columns: repeat(5, minmax(88px, 1fr));
}

.signal-rail article {
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 7px;
  padding: 24px 16px;
  border-left: 1px solid var(--ops-line);
}

.signal-rail article:first-child {
  border-left: 0;
}

.signal-rail span {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #718b95;
  font-size: 9px;
  white-space: nowrap;
}

.signal-rail span i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #50cfee;
}

.signal-rail span i.green {
  background: #4fd5a4;
}

.signal-rail span i.red {
  background: #ff7482;
}

.signal-rail span i.amber {
  background: #efbd6d;
}

.signal-rail strong {
  overflow: hidden;
  color: #eff9fb;
  font-size: clamp(21px, 2vw, 30px);
  letter-spacing: -0.04em;
  text-overflow: ellipsis;
}

.signal-rail small {
  color: #526d78;
  font-size: 8px;
}

.operation-routes {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-top: 1px solid var(--ops-line);
  border-bottom: 1px solid var(--ops-line);
}

.operation-routes > a {
  min-width: 0;
  min-height: 86px;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  align-content: center;
  gap: 4px 13px;
  padding: 14px 22px;
  color: inherit;
  border-right: 1px solid var(--ops-line);
  text-decoration: none;
  transition: background 140ms ease;
}

.operation-routes > a:last-child {
  border-right: 0;
}

.operation-routes > a:hover {
  background: rgba(80, 207, 238, 0.045);
}

.operation-routes > a.degraded {
  box-shadow: inset 0 -2px #ff7482;
}

.operation-routes > a > span {
  grid-row: 1 / 3;
  color: #3a8297;
  font-family: "JetBrains Mono", monospace;
  font-size: 9px;
}

.operation-routes div {
  min-width: 0;
  display: grid;
  gap: 5px;
}

.operation-routes strong {
  color: #dcebee;
  font-size: 12px;
}

.operation-routes small {
  overflow: hidden;
  color: #617c87;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.operation-routes b {
  grid-column: 2;
  color: #50cfee;
  font-size: 9px;
  font-weight: 650;
}

.compute-fabric,
.telemetry-panel,
.attention-panel,
.activity-ledger {
  overflow: hidden;
  border: 1px solid var(--ops-line);
  border-radius: 9px;
  background: rgba(10, 22, 29, 0.72);
}

.ops-section-heading {
  min-height: 84px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  padding: 18px 22px;
  border-bottom: 1px solid var(--ops-line);
}

.ops-section-heading.compact {
  min-height: 76px;
}

.ops-section-heading span {
  margin-bottom: 5px;
}

.ops-section-heading h2 {
  margin: 0;
  color: #e7f3f5;
  font-size: 17px;
  letter-spacing: -0.025em;
}

.ops-section-heading p {
  margin-top: 5px;
  font-size: 9px;
}

.ops-section-heading a,
.ops-section-heading > b {
  color: #50cfee;
  font-size: 9px;
  font-weight: 680;
  text-decoration: none;
}

.telemetry-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.7fr);
  gap: 16px;
}

#queue-chart {
  height: 300px;
}

.attention-clear {
  min-height: 300px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 7px;
  color: #58737e;
}

.attention-clear i {
  width: 8px;
  height: 8px;
  margin-bottom: 7px;
  border-radius: 50%;
  background: #4fd5a4;
  box-shadow: 0 0 17px rgba(79, 213, 164, 0.7);
}

.attention-clear strong {
  color: #b8cbd1;
  font-size: 11px;
}

.attention-clear span {
  font-size: 9px;
}

.attention-list {
  margin: 0;
  padding: 14px;
  list-style: none;
}

.attention-list li {
  padding: 12px;
  border-bottom: 1px solid var(--ops-line);
}

.attention-list li > span {
  color: #ff8994;
  font-size: 8px;
}

.attention-list strong {
  display: block;
  margin: 5px 0;
  color: #dce9ec;
  font-size: 11px;
}

.attention-list p {
  margin: 0;
  color: #6b858f;
  font-size: 9px;
  line-height: 1.5;
}

@keyframes ops-pulse {
  to {
    opacity: 0.3;
  }
}

@media (max-width: 1280px) {
  .situation-board {
    grid-template-columns: 1fr;
  }

  .situation-primary {
    border-right: 0;
    border-bottom: 1px solid var(--ops-line);
  }

  .signal-rail article {
    min-height: 118px;
  }
}

@media (max-width: 820px) {
  .ops-hero {
    align-items: flex-start;
    flex-direction: column;
    gap: 20px;
  }

  .ops-hero-controls {
    width: 100%;
    justify-content: space-between;
  }

  .situation-primary {
    min-height: 290px;
    grid-template-columns: 1fr;
    justify-items: start;
    padding-top: 58px;
  }

  .capacity-orbit {
    width: 110px;
    height: 110px;
  }

  .capacity-orbit::before {
    width: 92px;
    height: 92px;
  }

  .signal-rail {
    grid-template-columns: repeat(2, 1fr);
  }

  .signal-rail article {
    border-bottom: 1px solid var(--ops-line);
  }

  .operation-routes {
    grid-template-columns: 1fr;
  }

  .operation-routes > a {
    border-right: 0;
    border-bottom: 1px solid var(--ops-line);
  }

  .telemetry-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .ops-hero-controls {
    align-items: stretch;
    flex-direction: column;
  }

  .ops-scope button {
    flex: 1;
  }

  .ops-error {
    grid-template-columns: 1fr auto;
  }

  .ops-error span {
    grid-column: 1 / -1;
  }

  .situation-primary {
    padding-right: 20px;
    padding-left: 20px;
  }

  .signal-rail {
    grid-template-columns: 1fr;
  }

  .signal-rail article {
    min-height: 92px;
  }

  .ops-section-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
