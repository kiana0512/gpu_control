<script setup lang="ts">
import { LineChart } from "echarts/charts";
import { GridComponent } from "echarts/components";
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

const store = useSystemStore();
const router = useRouter();
const assetOverview = ref<AssetProcessingOverview | null>(null);
const assetError = ref("");
use([LineChart, GridComponent, CanvasRenderer]);

let chart: ECharts | undefined;

const metrics = computed(() => [
  { label: "排队任务", value: store.dashboard?.jobs.QUEUED ?? 0, tone: "blue" },
  { label: "运行中", value: store.dashboard?.jobs.RUNNING ?? 0, tone: "green" },
  {
    label: "今日成功",
    value: store.dashboard?.jobs.SUCCEEDED ?? 0,
    tone: "green",
  },
  { label: "今日失败", value: store.dashboard?.jobs.FAILED ?? 0, tone: "red" },
  {
    label: "最老等待",
    value: `${store.dashboard?.oldest_wait_seconds ?? 0}s`,
    tone: "amber",
  },
  {
    label: "预计清空",
    value:
      store.dashboard?.estimated_clear_seconds == null
        ? "样本不足"
        : `${Math.ceil(store.dashboard.estimated_clear_seconds / 60)}m`,
    tone: "blue",
  },
]);
const connectedNodes = computed(() =>
  store.nodes.filter(
    (node) => node.last_heartbeat_at || node.health !== "OFFLINE",
  ),
);
const assetActive = computed(() => {
  const counts = assetOverview.value?.summary.counts ?? {};
  return (counts.QUEUED ?? 0) + (counts.CLAIMED ?? 0) + (counts.RUNNING ?? 0);
});

function resizeChart() {
  chart?.resize();
}

function renderChart() {
  const element = document.getElementById("queue-chart");
  if (!element) return;
  chart ??= init(element);
  chart.setOption({
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: store.dashboard?.submission_trend.map((item) => item.label) ?? [],
      axisLabel: { color: "#8e91a8" },
      axisLine: { lineStyle: { color: "#353647" } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#8e91a8" },
      splitLine: { lineStyle: { type: "dashed", color: "#2b2c3b" } },
    },
    series: [
      {
        name: "排队",
        type: "line",
        smooth: 0.32,
        data: store.dashboard?.submission_trend.map((item) => item.value) ?? [],
        lineStyle: { color: "#c455f4", width: 3 },
        showSymbol: false,
        areaStyle: { color: "rgba(196,85,244,.12)" },
      },
    ],
  });
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
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>系统总览</h1>
        <p>GPU 推理与 CPU 资产处理两个独立平面的实时运行状态</p>
      </div>
      <div class="heading-actions">
        <div class="scope-tabs" aria-label="任务数据范围">
          <button
            :class="{ active: store.clientKind === 'production' }"
            @click="changeClientKind('production')"
          >
            真实业务
          </button>
          <button
            :class="{ active: store.clientKind === 'test' }"
            @click="changeClientKind('test')"
          >
            压力测试
          </button>
        </div>
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i>自动刷新 · 10 秒<br /><small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          ></span
        >
        <button class="primary" @click="run">
          {{ refreshing ? "刷新中…" : "立即刷新" }}
        </button>
      </div>
    </div>
    <div class="scope-notice" :class="{ test: store.clientKind === 'test' }">
      {{
        store.clientKind === "test"
          ? "当前仅展示测试客户与测试任务；这些数据不会计入真实业务总览。"
          : "当前仅展示真实客户与真实任务；压力测试流量已隔离且只使用真实业务空闲槽。"
      }}
    </div>
    <div v-if="store.error" class="error-banner">
      {{ store.error }} <button @click="refresh">重试</button>
    </div>
    <section class="metric-band blue-rail">
      <div v-for="metric in metrics" :key="metric.label">
        <span>{{ metric.label }}<i :class="metric.tone"></i></span
        ><strong>{{ metric.value }}</strong>
      </div>
    </section>
    <section class="dashboard-plane-summary">
      <div>
        <span>GPU 推理平面</span>
        <strong>{{ connectedNodes.length }} 个节点 · {{ store.jobs.length }} 个最近任务</strong>
        <small>ImageClip 抠图、ModelView 局部重绘、序列帧批次</small>
        <router-link to="/jobs">查看 GPU 任务 →</router-link>
      </div>
      <div :class="{ degraded: assetError }">
        <span>CPU 资产平面</span>
        <strong
          >{{ assetOverview?.summary.online_workers ?? 0 }} 个 Worker ·
          {{ assetActive }} 个处理中</strong
        >
        <small v-if="!assetError"
          >PBR UV、AI 重拓扑、阶段进度、四视图审核</small
        ><small v-else>{{ assetError }}</small>
        <router-link to="/asset-processing">查看资产任务 →</router-link>
      </div>
    </section>
    <NodeTable :nodes="connectedNodes" />
    <div class="dashboard-lower">
      <section class="ruled-section blue-rail chart-section">
        <div class="section-title">
          <h2>任务提交趋势（过去 6 小时）</h2>
          <span>自动刷新 10s</span>
        </div>
        <div id="queue-chart"></div>
      </section>
      <aside class="alerts blue-rail">
        <div class="section-title">
          <h2>最近告警</h2>
          <router-link to="/alerts">查看全部</router-link>
        </div>
        <div v-if="!store.dashboard?.active_alerts.length" class="alert-empty">
          <i></i><strong>当前无活动告警</strong
          ><span>所有节点指标均在阈值内</span>
        </div>
        <div v-else class="alert-empty">
          <strong>{{ store.dashboard.active_alerts[0].name }}</strong>
          <span>{{ store.dashboard.active_alerts[0].summary }}</span>
          <router-link to="/alerts">处理告警</router-link>
        </div>
      </aside>
    </div>
    <JobsTable :jobs="store.jobs" @select="openJob" />
  </div>
</template>
