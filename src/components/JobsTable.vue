<script setup lang="ts">
import type { JobInfo } from "../types";
import {
  compactTaskName,
  endToEndDuration,
  formatDateTime,
  formatDuration,
  gpuDuration,
  nodeSummary,
  queueDuration,
  serviceFor,
  type TaskJob,
} from "../jobPresentation";
import StatusMark from "./StatusMark.vue";

defineProps<{ jobs: JobInfo[] }>();
const emit = defineEmits<{ select: [job: JobInfo] }>();

function displayStatus(job: TaskJob) {
  return job.kind === "batch" && job.status === "CANCELLING" && job.error
    ? "FAILING"
    : job.status;
}
</script>

<template>
  <section class="task-list-panel" aria-label="GPU 任务列表">
    <div class="table-wrap">
      <table class="task-table">
        <colgroup>
          <col class="task-column" />
          <col class="service-column" />
          <col class="status-column" />
          <col class="timeline-column" />
          <col class="performance-column" />
          <col class="node-column" />
          <col class="action-column" />
        </colgroup>
        <thead>
          <tr>
            <th>任务 / 来源</th>
            <th>功能 / API</th>
            <th>状态 / 进度</th>
            <th>开始与结束</th>
            <th>阶段耗时</th>
            <th>执行节点</th>
            <th><span class="visually-hidden">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="rawJob in jobs"
            :key="rawJob.job_id"
            class="task-row"
            tabindex="0"
            @click="emit('select', rawJob)"
            @keydown.enter="emit('select', rawJob)"
          >
            <td>
              <button
                class="task-name"
                :title="rawJob.external_batch_id || rawJob.job_id"
                @click.stop="emit('select', rawJob)"
              >
                {{ compactTaskName(rawJob as TaskJob) }}
              </button>
              <div class="task-meta-line">
                <span v-if="rawJob.kind === 'batch'" class="kind-badge"
                  >序列帧批次</span
                >
                <span v-else class="kind-badge neutral">独立任务</span>
                <span v-if="rawJob.client_kind === 'test'" class="test-badge"
                  >压力测试</span
                >
              </div>
              <code :title="rawJob.job_id">{{ rawJob.job_id }}</code>
            </td>
            <td>
              <strong class="service-name">
                {{ serviceFor(rawJob as TaskJob).label }}
              </strong>
              <code class="api-path">
                {{ serviceFor(rawJob as TaskJob).api }}
              </code>
              <span class="workflow-version">
                {{ rawJob.workflow_key }} · {{ rawJob.workflow_version }}
              </span>
            </td>
            <td>
              <StatusMark :value="displayStatus(rawJob as TaskJob)" />
              <div class="task-progress" aria-label="任务进度">
                <i :style="{ width: `${rawJob.progress}%` }"></i>
              </div>
              <span class="progress-copy">
                <template v-if="rawJob.kind === 'batch' && rawJob.counts">
                  {{ rawJob.counts.succeeded }} / {{ rawJob.counts.total }} 帧
                </template>
                <template v-else>{{ rawJob.progress.toFixed(0) }}%</template>
              </span>
            </td>
            <td>
              <dl class="inline-facts timeline-facts">
                <div>
                  <dt>提交</dt>
                  <dd>{{ formatDateTime(rawJob.created_at) }}</dd>
                </div>
                <div>
                  <dt>开始</dt>
                  <dd>{{ formatDateTime(rawJob.started_at) }}</dd>
                </div>
                <div>
                  <dt>结束</dt>
                  <dd>{{ formatDateTime(rawJob.finished_at) }}</dd>
                </div>
              </dl>
            </td>
            <td>
              <dl class="inline-facts duration-facts">
                <div>
                  <dt>端到端</dt>
                  <dd class="duration-primary">
                    {{ formatDuration(endToEndDuration(rawJob as TaskJob)) }}
                  </dd>
                </div>
                <div>
                  <dt>真实排队</dt>
                  <dd>
                    {{ formatDuration(queueDuration(rawJob as TaskJob)) }}
                  </dd>
                </div>
                <div>
                  <dt>GPU wall</dt>
                  <dd>{{ formatDuration(gpuDuration(rawJob as TaskJob)) }}</dd>
                </div>
              </dl>
            </td>
            <td>
              <span
                class="node-summary"
                :title="nodeSummary(rawJob as TaskJob)"
              >
                {{ nodeSummary(rawJob as TaskJob) }}
              </span>
              <small v-if="rawJob.attempt > 1"
                >累计 {{ rawJob.attempt }} 次尝试</small
              >
            </td>
            <td>
              <button
                class="detail-button"
                aria-label="展开任务详情"
                @click.stop="emit('select', rawJob)"
              >
                详情
                <span aria-hidden="true">→</span>
              </button>
            </td>
          </tr>
          <tr v-if="!jobs.length">
            <td colspan="7" class="empty-task-list">
              <strong>没有符合当前筛选条件的任务</strong>
              <span>尝试清除筛选，或切换真实任务 / 测试任务范围。</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.task-list-panel {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid #2b3040;
  border-radius: 14px;
  background: #10131e;
  box-shadow: 0 20px 50px rgb(0 0 0 / 18%);
}

.table-wrap {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
}

.task-table {
  width: 100%;
  min-width: 1160px;
  table-layout: fixed;
  border-collapse: collapse;
  font-size: 14px;
}

.task-column {
  width: 190px;
}

.service-column {
  width: 220px;
}

.status-column {
  width: 125px;
}

.timeline-column {
  width: 180px;
}

.performance-column {
  width: 180px;
}

.node-column {
  width: 150px;
}

.action-column {
  width: 82px;
}

th {
  height: 48px;
  padding: 0 18px;
  color: #929bad;
  border-bottom: 1px solid #2b3040;
  background: #0d1019;
  font-size: 13px;
  font-weight: 650;
  text-align: left;
}

th:last-child,
td:last-child {
  position: sticky;
  right: 0;
  z-index: 2;
  box-shadow: -10px 0 18px rgb(5 7 13 / 20%);
}

th:last-child {
  z-index: 3;
  background: #0d1019;
}

td {
  min-height: 104px;
  padding: 17px 18px;
  color: #d8dde7;
  border-bottom: 1px solid #282d3b;
  background: #121621;
  vertical-align: top;
}

.task-row:last-child td {
  border-bottom: 0;
}

.task-row {
  cursor: pointer;
  outline: 0;
}

.task-row:hover td,
.task-row:focus td {
  background: #171a28;
}

.task-row td:last-child {
  background: #121621;
}

.task-row:hover td:last-child,
.task-row:focus td:last-child {
  background: #171a28;
}

.task-row:focus td:first-child {
  box-shadow: inset 3px 0 #dc4db4;
}

.task-name {
  display: block;
  max-width: 100%;
  overflow: hidden;
  padding: 0;
  color: #f8f7fb;
  border: 0;
  background: transparent;
  font-size: 15px;
  font-weight: 760;
  line-height: 1.35;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.task-meta-line {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.kind-badge,
.test-badge {
  display: inline-flex;
  align-items: center;
  min-height: 23px;
  padding: 0 8px;
  color: #ed91d5;
  border: 1px solid rgb(222 77 180 / 25%);
  border-radius: 999px;
  background: rgb(222 77 180 / 9%);
  font-size: 12px;
  font-weight: 650;
}

.kind-badge.neutral {
  color: #adb7c8;
  border-color: #353c4e;
  background: #191e2b;
}

.test-badge {
  color: #ffc66d;
  border-color: rgb(255 181 71 / 28%);
  background: rgb(255 181 71 / 8%);
}

.task-table td > code {
  display: block;
  max-width: 100%;
  overflow: hidden;
  margin-top: 8px;
  color: #7f899c;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.service-name {
  display: block;
  color: #f1eff5;
  font-size: 14px;
  line-height: 1.4;
}

.api-path {
  color: #d486ff !important;
  font-size: 12px !important;
}

.workflow-version {
  display: block;
  max-width: 100%;
  overflow: hidden;
  margin-top: 7px;
  color: #8993a5;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-progress {
  width: 100%;
  height: 7px;
  overflow: hidden;
  margin-top: 13px;
  border-radius: 999px;
  background: #2a2f3d;
}

.task-progress i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #9b50f4, #e84eb1);
}

.progress-copy {
  display: block;
  margin-top: 7px;
  color: #aeb7c7;
  font-size: 12px;
}

.inline-facts {
  display: grid;
  gap: 7px;
  margin: 0;
}

.inline-facts > div {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: baseline;
  gap: 8px;
}

.inline-facts dt,
.inline-facts dd {
  margin: 0;
}

.inline-facts dt {
  color: #7e899b;
  font-size: 12px;
}

.inline-facts dd {
  color: #cdd3de;
  font-size: 13px;
  white-space: nowrap;
}

.duration-facts > div {
  grid-template-columns: 70px minmax(0, 1fr);
}

.duration-primary {
  color: #f06abe !important;
  font-size: 15px !important;
  font-weight: 750;
}

.node-summary {
  display: -webkit-box;
  overflow: hidden;
  color: #cbd2dd;
  font-size: 13px;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.node-column + td small,
td > small {
  display: block;
  margin-top: 8px;
  color: #8892a3;
  font-size: 12px;
}

.detail-button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 0 11px;
  color: #f0c9ff;
  border: 1px solid rgb(182 92 235 / 36%);
  border-radius: 8px;
  background: rgb(177 74 232 / 10%);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.detail-button:hover {
  border-color: #cf5fef;
  background: rgb(207 95 239 / 18%);
}

.empty-task-list {
  height: 220px;
  text-align: center;
  vertical-align: middle;
}

.empty-task-list strong,
.empty-task-list span {
  display: block;
}

.empty-task-list strong {
  color: #edf0f6;
  font-size: 16px;
}

.empty-task-list span {
  margin-top: 8px;
  color: #8e98aa;
  font-size: 13px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

@media (max-width: 720px) {
  .task-list-panel {
    overflow: visible;
    border: 0;
    background: transparent;
    box-shadow: none;
  }

  .table-wrap {
    overflow: visible;
  }

  .task-table {
    display: block;
    width: 100%;
    min-width: 0;
    table-layout: auto;
  }

  colgroup,
  thead {
    display: none;
  }

  tbody,
  tbody > tr,
  td {
    display: block;
    width: 100%;
  }

  .task-row {
    overflow: hidden;
    margin-bottom: 12px;
    border: 1px solid #2b3040;
    border-radius: 12px;
    background: #121621;
  }

  .task-row:hover,
  .task-row:focus {
    border-color: #555d73;
  }

  td {
    height: auto;
    min-height: 0;
    padding: 13px 15px;
    border: 0;
    border-bottom: 1px solid #282d3b;
  }

  td:last-child {
    position: static;
    box-shadow: none;
  }

  .task-row td:last-child {
    border-bottom: 0;
  }

  .task-name {
    font-size: 16px;
  }

  .service-name,
  .inline-facts dd,
  .node-summary {
    font-size: 14px;
  }

  .detail-button {
    width: 100%;
    justify-content: center;
  }

  .empty-task-list {
    display: block;
    height: auto;
    padding: 44px 20px;
    border: 1px solid #2b3040;
    border-radius: 12px;
  }
}
</style>
