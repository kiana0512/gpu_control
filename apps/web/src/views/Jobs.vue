<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { JobInfo } from "../types";
import JobsTable from "../components/JobsTable.vue";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";
const jobs = ref<JobInfo[]>([]);
const route = useRoute();
const loading = ref(false);
const error = ref("");
const selectedJob = ref<JobInfo | null>(null);
const actionBusy = ref("");
const terminalStatuses = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"];
const canCancel = computed(
  () =>
    selectedJob.value && !terminalStatuses.includes(selectedJob.value.status),
);
const canRetry = computed(
  () =>
    selectedJob.value &&
    ["FAILED", "TIMED_OUT"].includes(selectedJob.value.status),
);
async function load() {
  loading.value = true;
  error.value = "";
  try {
    jobs.value = await api.jobs();
    const requestedJob =
      typeof route.query.job === "string" ? route.query.job : "";
    if (requestedJob && !selectedJob.value)
      selectedJob.value =
        jobs.value.find((job) => job.job_id === requestedJob) ?? null;
  } catch (e) {
    error.value = e instanceof Error ? e.message : "任务数据加载失败";
    throw e;
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
    await api.cancel(id);
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
function formatTime(value: string | null) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
function selectJob(job: JobInfo) {
  selectedJob.value = job;
}
const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>任务中心</h1>
        <p>查询状态、进度、节点、重试与诊断信息</p>
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
      <strong>任务同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>
    <JobsTable :jobs="jobs" @select="selectJob" />

    <div
      v-if="selectedJob"
      class="panel-backdrop task-backdrop"
      @click.self="selectedJob = null"
    >
      <aside
        class="task-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
      >
        <header>
          <div>
            <span class="eyebrow">任务详情</span>
            <h2>{{ selectedJob.job_id.slice(0, 13) }}</h2>
            <p>
              {{ selectedJob.workflow_key }} · v{{
                selectedJob.workflow_version
              }}
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
        <div class="task-result" :class="selectedJob.status.toLowerCase()">
          <StatusMark :value="selectedJob.status" /><strong>{{
            selectedJob.status === "SUCCEEDED"
              ? "任务处理成功，图片已返回调用方"
              : selectedJob.status === "FAILED"
                ? "任务执行失败，请查看下方错误信息"
                : "任务状态已同步"
          }}</strong
          ><span>进度 {{ selectedJob.progress.toFixed(0) }}%</span>
        </div>
        <dl class="task-facts">
          <dt>完整任务 ID</dt>
          <dd>
            <code>{{ selectedJob.job_id }}</code>
          </dd>
          <dt>执行节点</dt>
          <dd>{{ selectedJob.node_id || "尚未分配" }}</dd>
          <dt>ComfyUI Prompt</dt>
          <dd>
            <code>{{ selectedJob.prompt_id || "—" }}</code>
          </dd>
          <dt>尝试次数</dt>
          <dd>{{ selectedJob.attempt }}</dd>
          <dt>提交时间</dt>
          <dd>{{ formatTime(selectedJob.created_at) }}</dd>
          <dt>开始时间</dt>
          <dd>{{ formatTime(selectedJob.started_at) }}</dd>
          <dt>完成时间</dt>
          <dd>{{ formatTime(selectedJob.finished_at) }}</dd>
        </dl>
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
                ? "同步图片 API 已把最终 PNG 直接作为 HTTP 响应返回给调用软件；管理端保留任务记录和诊断信息。"
                : "状态会每 10 秒自动刷新。运行中的任务可以取消，失败任务可以安全重试。"
            }}
          </p>
        </section>
        <footer>
          <button
            class="secondary"
            :disabled="Boolean(actionBusy)"
            @click="diagnostics(selectedJob.job_id)"
          >
            {{
              actionBusy === "diagnostics" ? "正在生成…" : "下载诊断包"
            }}</button
          ><button
            v-if="canRetry"
            class="primary"
            :disabled="Boolean(actionBusy)"
            @click="retry(selectedJob.job_id)"
          >
            重试任务</button
          ><button
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
