<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { JobInfo } from "../types";
import JobsTable from "../components/JobsTable.vue";
const jobs = ref<JobInfo[]>([]);
const loading = ref(false);
async function load() {
  loading.value = true;
  try {
    jobs.value = await api.jobs();
  } finally {
    loading.value = false;
  }
}
async function retry(id: string) {
  await ElMessageBox.confirm(
    "确认重试这个任务？系统不会对已提交且状态未知的 prompt 盲目重提。",
    "二次确认",
    { type: "warning" },
  );
  await api.retry(id);
  ElMessage.success("任务已重新排队");
  await load();
}
async function cancel(id: string) {
  await ElMessageBox.confirm(
    "确认取消这个任务？运行中的 ComfyUI prompt 会被中断。",
    "取消任务",
    {
      type: "warning",
    },
  );
  await api.cancel(id);
  ElMessage.success("取消请求已提交");
  await load();
}
async function diagnostics(id: string) {
  const blob = await api.diagnostics(id);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${id}-diagnostics.zip`;
  link.click();
  URL.revokeObjectURL(url);
}
onMounted(load);
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>任务中心</h1>
        <p>查询状态、进度、节点、重试与诊断信息</p>
      </div>
      <button class="secondary" @click="load">刷新</button>
    </div>
    <JobsTable :jobs="jobs" />
    <div
      class="operation-list"
      v-if="jobs.some((j) => j.status === 'FAILED' || j.status === 'TIMED_OUT')"
    >
      <h2>可重试任务</h2>
      <button
        v-for="job in jobs.filter(
          (j) => j.status === 'FAILED' || j.status === 'TIMED_OUT',
        )"
        :key="job.job_id"
        @click="retry(job.job_id)"
      >
        重试 {{ job.job_id.slice(0, 8) }}
      </button>
    </div>
    <div class="operation-list" v-if="jobs.length">
      <h2>任务操作</h2>
      <template v-for="job in jobs" :key="job.job_id">
        <button
          v-if="
            !['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT'].includes(
              job.status,
            )
          "
          @click="cancel(job.job_id)"
        >
          取消 {{ job.job_id.slice(0, 8) }}
        </button>
        <button @click="diagnostics(job.job_id)">
          诊断包 {{ job.job_id.slice(0, 8) }}
        </button>
      </template>
    </div>
  </div>
</template>
