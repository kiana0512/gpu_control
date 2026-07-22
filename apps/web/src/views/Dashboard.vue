<script setup lang="ts">
import { LineChart } from "echarts/charts";
import { GridComponent } from "echarts/components";
import { init, use, type ECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { computed, onBeforeUnmount, onMounted } from "vue";

import JobsTable from "../components/JobsTable.vue";
import NodeTable from "../components/NodeTable.vue";
import { useSystemStore } from "../stores/system";

const store = useSystemStore();
let timer: number | undefined;
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

function resizeChart() {
  chart?.resize();
}

function renderChart() {
  const element = document.getElementById("queue-chart");
  if (!element) return;
  chart = init(element);
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

onMounted(async () => {
  await store.refresh();
  renderChart();
  timer = window.setInterval(() => store.refresh(), 10_000);
  window.addEventListener("resize", resizeChart);
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  window.removeEventListener("resize", resizeChart);
  chart?.dispose();
});
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>系统总览</h1>
        <p>三节点计算池与任务队列的实时运行状态</p>
      </div>
      <button class="primary" @click="store.refresh">
        {{ store.loading ? "刷新中…" : "刷新数据" }}
      </button>
    </div>
    <div v-if="store.error" class="error-banner">
      {{ store.error }} <button @click="store.refresh">重试</button>
    </div>
    <section class="metric-band blue-rail">
      <div v-for="metric in metrics" :key="metric.label">
        <span>{{ metric.label }}<i :class="metric.tone"></i></span
        ><strong>{{ metric.value }}</strong>
      </div>
    </section>
    <NodeTable :nodes="store.nodes" />
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
    <JobsTable :jobs="store.jobs" />
  </div>
</template>
