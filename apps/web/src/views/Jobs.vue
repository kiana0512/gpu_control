<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";
import type { BatchItem, JobInfo } from "../types";
import JobsTable from "../components/JobsTable.vue";
import StatusMark from "../components/StatusMark.vue";
import { useAutoRefresh } from "../composables/useAutoRefresh";
const jobs = ref<JobInfo[]>([]);
const route = useRoute();
const loading = ref(false);
const error = ref("");
const selectedJob = ref<JobInfo | null>(null);
const batchItems = ref<BatchItem[]>([]);
const batchOffset = ref(0);
const batchItemsTotal = ref(0);
const batchLoading = ref(false);
const batchPageSize = 100;
const actionBusy = ref("");
const clientKind = ref<"production" | "test">(
  route.query.kind === "test" ? "test" : "production",
);
const currentPage = ref(1);
const pageSize = ref(20);
const pageCount = computed(() =>
  Math.max(1, Math.ceil(jobs.value.length / pageSize.value)),
);
const pagedJobs = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value;
  return jobs.value.slice(start, start + pageSize.value);
});
watch(pageCount, (count) => {
  if (currentPage.value > count) currentPage.value = count;
});
const terminalStatuses = ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"];
const isBatch = computed(() => selectedJob.value?.kind === "batch");
const canCancel = computed(
  () =>
    selectedJob.value && !terminalStatuses.includes(selectedJob.value.status),
);
const canRetry = computed(
  () =>
    selectedJob.value &&
    selectedJob.value.kind !== "batch" &&
    ["FAILED", "TIMED_OUT"].includes(selectedJob.value.status),
);
async function loadBatch(id: string, offset = batchOffset.value) {
  batchLoading.value = true;
  try {
    const [detail, page] = await Promise.all([
      api.batch(id),
      api.batchItems(id, offset, batchPageSize),
    ]);
    selectedJob.value = detail;
    batchItems.value = page.items;
    batchItemsTotal.value = page.total;
    batchOffset.value = page.offset;
  } finally {
    batchLoading.value = false;
  }
}
async function load() {
  loading.value = true;
  error.value = "";
  try {
    jobs.value = await api.jobs(undefined, clientKind.value);
    const requestedJob =
      typeof route.query.job === "string" ? route.query.job : "";
    if (requestedJob && !selectedJob.value)
      selectedJob.value =
        jobs.value.find((job) => job.job_id === requestedJob) ?? null;
    if (selectedJob.value?.kind === "batch") {
      await loadBatch(selectedJob.value.job_id);
    } else if (selectedJob.value) {
      selectedJob.value =
        jobs.value.find((job) => job.job_id === selectedJob.value?.job_id) ??
        selectedJob.value;
    }
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
    if (isBatch.value) await api.cancelBatch(id);
    else await api.cancel(id);
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
async function selectJob(job: JobInfo) {
  selectedJob.value = job;
  batchItems.value = [];
  batchOffset.value = 0;
  batchItemsTotal.value = 0;
  if (job.kind === "batch") await loadBatch(job.job_id, 0);
}
async function changeClientKind(kind: "production" | "test") {
  if (clientKind.value === kind) return;
  clientKind.value = kind;
  currentPage.value = 1;
  selectedJob.value = null;
  await run();
}
async function changeBatchPage(offset: number) {
  if (!selectedJob.value || selectedJob.value.kind !== "batch") return;
  await loadBatch(
    selectedJob.value.job_id,
    Math.max(0, Math.min(offset, Math.max(0, batchItemsTotal.value - 1))),
  );
}
function nodeDistribution(job: JobInfo) {
  const entries = Object.entries(job.node_distribution ?? {});
  return entries.length
    ? entries.map(([node, count]) => `${node}：${count} 帧`).join("，")
    : "尚未分配";
}
const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>
<template>
  <div class="page">
    <div class="page-heading">
      <div>
        <h1>任务中心</h1>
        <p>GPU 推理与 CPU 资产任务分平面运行、统一入口管理</p>
        <div class="task-plane-switch" aria-label="任务平面">
          <router-link class="active" to="/jobs">GPU 推理任务</router-link>
          <router-link to="/asset-processing">CPU 资产任务</router-link>
        </div>
      </div>
      <div class="heading-actions">
        <div class="scope-tabs" aria-label="任务数据范围">
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
    <div class="scope-notice" :class="{ test: clientKind === 'test' }">
      {{
        clientKind === "test"
          ? "仅显示压力测试任务，不会与真实任务混在一起。"
          : "仅显示真实业务任务；压力测试任务已隐藏。"
      }}
    </div>
    <div v-if="error" class="error-banner persistent-error">
      <strong>任务同步失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>
    <JobsTable :jobs="pagedJobs" @select="selectJob" />
    <nav class="table-pagination" aria-label="GPU 任务分页">
      <span>共 {{ jobs.length }} 条 · 第 {{ currentPage }} / {{ pageCount }} 页</span>
      <label>
        每页
        <select v-model.number="pageSize" @change="currentPage = 1">
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
      </label>
      <button class="secondary" :disabled="currentPage <= 1" @click="currentPage--">上一页</button>
      <button class="secondary" :disabled="currentPage >= pageCount" @click="currentPage++">下一页</button>
    </nav>

    <div
      v-if="selectedJob"
      class="panel-backdrop task-backdrop"
      @click.self="selectedJob = null"
    >
      <aside
        class="task-drawer"
        :class="{ 'batch-drawer': isBatch }"
        role="dialog"
        aria-modal="true"
        aria-label="任务详情"
      >
        <header>
          <div>
            <span class="eyebrow">{{ isBatch ? "批次详情" : "任务详情" }}</span>
            <h2>
              {{
                selectedJob.external_batch_id || selectedJob.job_id.slice(0, 13)
              }}
            </h2>
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
              ? isBatch
                ? "全部序列帧已校验并生成完整结果包"
                : "任务处理成功，图片已返回调用方"
              : selectedJob.status === "FAILED"
                ? isBatch
                  ? "批次失败，未向调用方暴露不完整结果"
                  : "任务执行失败，请查看下方错误信息"
                : isBatch
                  ? "批次状态与逐帧进度已同步"
                  : "任务状态已同步"
          }}</strong
          ><span>进度 {{ selectedJob.progress.toFixed(0) }}%</span>
        </div>
        <dl class="task-facts" :class="{ 'batch-facts': isBatch }">
          <dt>{{ isBatch ? "完整批次 ID" : "完整任务 ID" }}</dt>
          <dd>
            <code>{{ selectedJob.job_id }}</code>
          </dd>
          <template v-if="isBatch">
            <dt>动画管家批次</dt>
            <dd>
              <code>{{ selectedJob.external_batch_id }}</code>
            </dd>
            <dt>序列帧进度</dt>
            <dd>
              {{ selectedJob.counts?.succeeded ?? 0 }} /
              {{ selectedJob.counts?.total ?? 0 }} 成功，{{
                selectedJob.counts?.running ?? 0
              }}
              运行， {{ selectedJob.counts?.failed ?? 0 }} 失败
            </dd>
            <dt>节点分配</dt>
            <dd>{{ nodeDistribution(selectedJob) }}</dd>
          </template>
          <template v-else>
            <dt>执行节点</dt>
            <dd>{{ selectedJob.node_id || "尚未分配" }}</dd>
            <dt>ComfyUI Prompt</dt>
            <dd>
              <code>{{ selectedJob.prompt_id || "—" }}</code>
            </dd>
          </template>
          <dt>{{ isBatch ? "累计尝试" : "尝试次数" }}</dt>
          <dd>{{ selectedJob.attempt }}</dd>
          <dt>提交时间</dt>
          <dd>{{ formatTime(selectedJob.created_at) }}</dd>
          <dt>开始时间</dt>
          <dd>{{ formatTime(selectedJob.started_at) }}</dd>
          <dt>完成时间</dt>
          <dd>
            {{ formatTime(selectedJob.finished_at) }}
          </dd>
        </dl>
        <section v-if="isBatch" class="batch-items-panel">
          <div class="batch-items-heading">
            <div>
              <h3>序列帧详情</h3>
              <p>逐帧状态仅在此处展示，不进入任务列表。</p>
            </div>
            <span v-if="batchLoading">正在同步…</span>
          </div>
          <div class="batch-items-scroll">
            <table>
              <thead>
                <tr>
                  <th>序号</th>
                  <th>输入路径</th>
                  <th>状态</th>
                  <th>节点</th>
                  <th>尝试</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in batchItems" :key="item.ordinal">
                  <td>{{ item.ordinal }}</td>
                  <td :title="item.input_relative_path">
                    <code>{{ item.input_relative_path }}</code>
                    <small v-if="item.error"
                      >{{ item.error.code }} · {{ item.error.message }}</small
                    >
                  </td>
                  <td><StatusMark :value="item.status" /></td>
                  <td>{{ item.node_id || "—" }}</td>
                  <td>{{ item.attempts }}</td>
                </tr>
                <tr v-if="!batchItems.length">
                  <td colspan="5" class="empty">尚无已物化帧</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="batch-pagination">
            <span>
              {{ batchItemsTotal ? batchOffset + 1 : 0 }}–{{
                Math.min(batchOffset + batchItems.length, batchItemsTotal)
              }}
              / {{ batchItemsTotal }}
            </span>
            <button
              class="secondary"
              :disabled="batchOffset === 0 || batchLoading"
              @click="changeBatchPage(batchOffset - batchPageSize)"
            >
              上一页
            </button>
            <button
              class="secondary"
              :disabled="
                batchOffset + batchPageSize >= batchItemsTotal || batchLoading
              "
              @click="changeBatchPage(batchOffset + batchPageSize)"
            >
              下一页
            </button>
          </div>
        </section>
        <section
          v-if="isBatch && selectedJob.artifacts?.length"
          class="batch-artifacts"
        >
          <h3>完整结果归档</h3>
          <a
            v-for="artifact in selectedJob.artifacts"
            :key="artifact.id"
            :href="artifact.download_url"
            target="_blank"
            rel="noopener"
          >
            <strong>{{ artifact.filename }}</strong>
            <span
              >{{ (artifact.size_bytes / 1024 / 1024).toFixed(1) }} MiB</span
            >
            <code>SHA-256 {{ artifact.sha256 }}</code>
          </a>
        </section>
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
                ? isBatch
                  ? "结果 ZIP 仅在全部帧、路径、顺序、SHA-256 和 Alpha 校验通过后发布给动画管家。"
                  : "同步图片 API 已把最终 PNG 直接作为 HTTP 响应返回给调用软件；管理端保留任务记录和诊断信息。"
                : "状态会每 10 秒自动刷新。运行中的任务可以取消，失败任务可以安全重试。"
            }}
          </p>
        </section>
        <footer>
          <button
            v-if="!isBatch"
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
