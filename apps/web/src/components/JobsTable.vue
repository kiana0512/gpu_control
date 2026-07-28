<script setup lang="ts">
import type { JobInfo } from "../types";
import StatusMark from "./StatusMark.vue";
defineProps<{ jobs: JobInfo[] }>();
const emit = defineEmits<{ select: [job: JobInfo] }>();
function isBatch(job: JobInfo) {
  return job.kind === "batch";
}
function nodeSummary(job: JobInfo) {
  if (!isBatch(job)) return job.node_id ?? "—";
  const entries = Object.entries(job.node_distribution ?? {});
  return entries.length
    ? entries.map(([node, count]) => `${node} · ${count}`).join(" / ")
    : "尚未分配";
}
</script>
<template>
  <section class="ruled-section blue-rail jobs-table">
    <div class="section-title">
      <h2>最近任务</h2>
      <router-link to="/jobs">查看全部 →</router-link>
    </div>
    <div class="table-wrap">
      <table>
        <colgroup>
          <col class="job-col-id" />
          <col class="job-col-workflow" />
          <col class="job-col-status" />
          <col class="job-col-node" />
          <col class="job-col-progress" />
          <col class="job-col-time" />
          <col class="job-col-action" />
        </colgroup>
        <thead>
          <tr>
            <th>任务 / 批次 ID</th>
            <th>工作流</th>
            <th>状态</th>
            <th>节点</th>
            <th>进度</th>
            <th>等待时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="job in jobs"
            :key="job.job_id"
            class="job-row"
            tabindex="0"
            @click="emit('select', job)"
            @keydown.enter="emit('select', job)"
          >
            <td>
              <button class="job-id-link" @click.stop="emit('select', job)">
                {{ job.external_batch_id || job.job_id.slice(0, 13) }}
              </button>
              <small v-if="isBatch(job)" class="batch-row-label"
                >序列帧批次</small
              >
              <small v-if="job.client_kind === 'test'" class="test-row-label"
                >压力测试</small
              >
            </td>
            <td>
              {{ job.workflow_key }} v{{ job.workflow_version }}
              <small v-if="isBatch(job) && job.counts" class="batch-row-label"
                >{{ job.counts.succeeded }} / {{ job.counts.total }} 帧</small
              >
            </td>
            <td><StatusMark :value="job.status" /></td>
            <td>{{ nodeSummary(job) }}</td>
            <td>
              <div class="progress">
                <i :style="{ width: `${job.progress}%` }"></i>
              </div>
              <small>{{ job.progress.toFixed(0) }}%</small>
            </td>
            <td>{{ new Date(job.created_at).toLocaleTimeString() }}</td>
            <td>
              <button
                class="row-action"
                aria-label="查看任务详情"
                @click.stop="emit('select', job)"
              >
                查看
              </button>
            </td>
          </tr>
          <tr v-if="!jobs.length">
            <td colspan="7" class="empty">暂无任务</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
