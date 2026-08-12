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
const connectedNodes = computed(() => {
  const order = [
    "control-4090",
    "worker-3090-a",
    "worker-3090-b",
    "worker-4070ti-animation-host-01",
  ];
  return [...store.nodes].sort((left, right) => {
    const leftIndex = order.indexOf(left.id);
    const rightIndex = order.indexOf(right.id);
    return (
      (leftIndex === -1 ? order.length : leftIndex) -
        (rightIndex === -1 ? order.length : rightIndex) ||
      left.display_name.localeCompare(right.display_name, "zh-CN")
    );
  });
});
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
    return "GPU 集群正在降级接单";
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
        backgroundColor: "#171b28",
        borderColor: "#343a4a",
        textStyle: { color: "#f7f5fb", fontSize: 13 },
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
        axisLabel: { color: "#8f98aa", fontSize: 12 },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#303644" } },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: "#8f98aa", fontSize: 12 },
        splitLine: { lineStyle: { type: "dashed", color: "#292f3d" } },
      },
      series: [
        {
          name: "排队",
          type: "line",
          smooth: 0.32,
          data:
            store.dashboard?.submission_trend.map((item) => item.value) ?? [],
          lineStyle: { color: "#df4ab5", width: 3 },
          showSymbol: false,
          areaStyle: { color: "rgba(223,74,181,.1)" },
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
  <div class="page dashboard-page">
    <div class="page-heading dashboard-heading">
      <div>
        <h1>生产运行总览</h1>
        <p>先判断队列、任务与节点是否正常，再进入对应页面处理问题。</p>
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
        <button type="button" class="secondary" @click="run">
          {{ refreshing ? "刷新中…" : "立即刷新" }}
        </button>
      </div>
    </div>
    <div class="scope-notice" :class="{ test: store.clientKind === 'test' }">
      {{
        store.clientKind === "test"
          ? "当前仅展示测试客户与测试任务；这只是数据范围隔离，压测是否发流量仍由独立执行门禁控制。"
          : "当前仅展示真实客户与真实任务；本页不会启动压力测试，也不代表调度器自动为测试流量降级优先级。"
      }}
    </div>
    <div v-if="store.error" class="error-banner">
      {{ store.error }} <button @click="refresh">重试</button>
    </div>
    <section class="dashboard-metrics" aria-label="关键运行指标">
      <article v-for="metric in metrics" :key="metric.label">
        <span><i :class="metric.tone"></i>{{ metric.label }}</span>
        <strong>{{ metric.value }}</strong>
        <small>{{ metric.hint }}</small>
      </article>
    </section>

    <section class="operations-brief" :class="clusterTone">
      <div class="operations-status">
        <span><i></i>实时判断</span>
        <h2>{{ clusterHeadline }}</h2>
        <p>
          调度器只会把新任务交给在线、ACTIVE
          且工作流兼容的节点；运行中的任务不会被这个页面改变。
        </p>
      </div>
      <dl class="operations-facts">
        <div>
          <dt>可接 GPU 节点</dt>
          <dd>{{ activeGpuNodes.length }} / {{ connectedNodes.length }}</dd>
        </div>
        <div>
          <dt>空闲 GPU 槽位</dt>
          <dd>{{ freeGpuSlots }} / {{ activeGpuSlots }}</dd>
        </div>
        <div>
          <dt>队列预计清空</dt>
          <dd>{{ queueClearText }}</dd>
        </div>
      </dl>
    </section>

    <section class="dashboard-plane-summary" aria-label="任务平面入口">
      <article>
        <div>
          <span>GPU 推理</span>
          <strong>{{ store.jobs.length }} 个最近任务</strong>
          <small>ImageClip 抠图、ModelView 重绘与序列帧批次</small>
        </div>
        <router-link to="/jobs">进入任务中心 <b>→</b></router-link>
      </article>
      <article :class="{ degraded: assetError }">
        <div>
          <span>CPU 资产处理</span>
          <strong
            >{{ assetOverview?.summary.online_workers ?? 0 }} 个 Worker ·
            {{ assetActive }} 个处理中</strong
          >
          <small v-if="!assetError">UV、AI 重拓扑与 PBR 烘焙</small>
          <small v-else>{{ assetError }}</small>
        </div>
        <router-link to="/asset-processing">进入资产任务 <b>→</b></router-link>
      </article>
    </section>

    <NodeTable :nodes="connectedNodes" />

    <div class="dashboard-lower">
      <section class="ruled-section chart-section">
        <div class="section-title dashboard-section-title">
          <div>
            <h2>任务流入趋势</h2>
            <p>过去 6 小时共提交 {{ trendTotal }} 个任务</p>
          </div>
          <span>每小时</span>
        </div>
        <div id="queue-chart"></div>
      </section>
      <aside class="alerts">
        <div class="section-title dashboard-section-title">
          <div>
            <h2>需要关注</h2>
            <p>当前活动告警</p>
          </div>
          <router-link to="/alerts">查看全部</router-link>
        </div>
        <div v-if="!store.dashboard?.active_alerts.length" class="alert-empty">
          <i></i><strong>当前没有活动告警</strong>
          <span>节点和控制平面指标均在阈值内</span>
        </div>
        <ul v-else class="dashboard-alert-list">
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

    <section class="recent-jobs-section">
      <header>
        <div>
          <h2>最近任务</h2>
          <p>开始、结束、排队与 GPU 耗时均可在任务行中直接查看。</p>
        </div>
        <router-link to="/jobs">查看全部任务 <b>→</b></router-link>
      </header>
      <JobsTable :jobs="store.jobs.slice(0, 8)" @select="openJob" />
    </section>
  </div>
</template>
