<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { useAutoRefresh } from "../composables/useAutoRefresh";

type LogRow = {
  id: string;
  kind: "job" | "audit";
  time: string;
  level: "信息" | "警告" | "错误";
  service: string;
  summary: string;
  jobId?: string;
  requestId?: string;
  nodeId?: string;
  sourceIp?: string;
  errorCode?: string;
  details: Record<string, unknown>;
};

const rows = ref<LogRow[]>([]);
const selected = ref<LogRow | null>(null);
const error = ref("");
const filters = reactive({ keyword: "", kind: "all", level: "all" });

const statusText: Record<string, string> = {
  RECEIVED: "任务已接收",
  QUEUED: "任务进入等待队列",
  CLAIMED: "调度器已分配 GPU",
  UPLOADING: "正在上传输入图片",
  SUBMITTED: "已提交到 ComfyUI",
  RUNNING: "ComfyUI 正在生成",
  DOWNLOADING: "正在下载生成结果",
  SUCCEEDED: "任务生成成功",
  FAILED: "任务生成失败",
  TIMED_OUT: "任务执行超时",
  CANCELLED: "任务已取消",
  CANCELLING: "正在取消任务",
  RETRY_WAIT: "任务等待重试",
};

function levelForStatus(status: string): LogRow["level"] {
  if (["FAILED", "TIMED_OUT"].includes(status)) return "错误";
  if (["CANCELLED", "CANCELLING", "RETRY_WAIT"].includes(status)) return "警告";
  return "信息";
}

function auditSummary(action: string, target: string) {
  const actions: Record<string, string> = {
    "node.mode.change": "管理员修改了 GPU 节点状态",
    "node.models.free": "管理员释放了节点模型显存",
    "job.cancel": "管理员取消了任务",
    "job.retry": "管理员重试了任务",
    "workflow.import": "管理员导入了工作流",
    "workflow.enable": "管理员启用了工作流",
    "workflow.disable": "管理员停用了工作流",
    "setting.update": "管理员更新了调度策略",
  };
  return `${actions[action] ?? action}${target ? ` · ${target}` : ""}`;
}

async function load() {
  error.value = "";
  try {
    const [jobs, audits] = await Promise.all([api.jobs(), api.audits()]);
    const jobRows: LogRow[] = jobs.map((job) => ({
      id: `job-${job.job_id}`,
      kind: "job",
      time: job.finished_at ?? job.started_at ?? job.created_at,
      level: levelForStatus(job.status),
      service: job.node_id ? "任务调度 / ComfyUI" : "任务调度",
      summary: statusText[job.status] ?? job.status,
      jobId: job.job_id,
      nodeId: job.node_id ?? undefined,
      errorCode: job.error?.code,
      details: {
        状态: job.status,
        工作流: `${job.workflow_key}:${job.workflow_version}`,
        进度: `${job.progress}%`,
        执行节点: job.node_id ?? "尚未分配",
        ComfyUI任务ID: job.prompt_id ?? "尚未提交",
        重试次数: job.attempt,
        错误信息: job.error?.message ?? "无",
      },
    }));
    const auditRows: LogRow[] = (
      audits as unknown as Record<string, unknown>[]
    ).map((audit) => ({
      id: `audit-${String(audit.id)}`,
      kind: "audit",
      time: String(audit.created_at),
      level: String(audit.result) === "SUCCESS" ? "信息" : "警告",
      service: "管理控制台",
      summary: auditSummary(
        String(audit.action),
        String(audit.target_id ?? ""),
      ),
      requestId: String(audit.request_id ?? "") || undefined,
      sourceIp: String(audit.source_ip ?? "") || undefined,
      details: {
        操作人: audit.actor_id,
        操作类型: audit.action,
        目标类型: audit.target_type,
        目标ID: audit.target_id,
        来源IP: audit.source_ip,
        请求ID: audit.request_id,
        操作前: audit.before,
        操作后: audit.after,
        结果: audit.result,
      },
    }));
    rows.value = [...jobRows, ...auditRows].sort(
      (a, b) => new Date(b.time).getTime() - new Date(a.time).getTime(),
    );
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "日志加载失败";
    throw cause;
  }
}

const visibleRows = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase();
  return rows.value.filter((row) => {
    if (filters.kind !== "all" && row.kind !== filters.kind) return false;
    if (filters.level !== "all" && row.level !== filters.level) return false;
    if (!keyword) return true;
    return JSON.stringify(row).toLowerCase().includes(keyword);
  });
});

async function openGrafana(row?: LogRow) {
  const target = row ?? selected.value;
  const key = target?.jobId ? "job_id" : target?.requestId ? "request_id" : "";
  const value = target?.jobId ?? target?.requestId ?? "";
  if (!key || !value) {
    ElMessage.info("请先选择一条带任务 ID 或请求 ID 的记录");
    return;
  }
  const result = await api.logLink(`${key}=${encodeURIComponent(value)}`);
  window.open(result.url, "_blank", "noopener,noreferrer");
}

async function copy(value: string) {
  if (window.navigator.clipboard && window.isSecureContext) {
    await window.navigator.clipboard.writeText(value);
  } else {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  }
  ElMessage.success("已复制");
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatDetail(value: unknown) {
  return typeof value === "object"
    ? JSON.stringify(value, null, 2)
    : String(value ?? "—");
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(load);
</script>

<template>
  <div class="page logs-page">
    <div class="page-heading">
      <div>
        <h1>日志中心</h1>
        <p>直接查看真实任务状态与管理员操作记录</p>
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
        ><button class="secondary" @click="run">立即刷新</button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>日志加载失败</strong><span>{{ error }}</span
      ><button @click="run">重试</button>
    </div>

    <section class="log-surface">
      <div class="log-toolbar">
        <div class="search-field">
          <span>⌕</span
          ><input
            v-model="filters.keyword"
            placeholder="搜索任务 ID、请求 ID、来源 IP、节点或错误码"
          />
        </div>
        <select v-model="filters.kind">
          <option value="all">全部来源</option>
          <option value="job">任务记录</option>
          <option value="audit">管理操作</option>
        </select>
        <select v-model="filters.level">
          <option value="all">全部级别</option>
          <option value="信息">信息</option>
          <option value="警告">警告</option>
          <option value="错误">错误</option>
        </select>
        <span class="record-count">{{ visibleRows.length }} 条真实记录</span>
      </div>

      <div v-if="visibleRows.length" class="table-wrap">
        <table class="log-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>级别</th>
              <th>来源</th>
              <th>事件</th>
              <th>任务 / 请求</th>
              <th>节点 / IP</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in visibleRows"
              :key="row.id"
              @click="selected = row"
            >
              <td>{{ formatTime(row.time) }}</td>
              <td>
                <span class="log-level" :class="row.level">{{
                  row.level
                }}</span>
              </td>
              <td>{{ row.service }}</td>
              <td>
                <strong>{{ row.summary }}</strong
                ><small v-if="row.errorCode">{{ row.errorCode }}</small>
              </td>
              <td>
                <code>{{ row.jobId || row.requestId || "—" }}</code>
              </td>
              <td>{{ row.nodeId || row.sourceIp || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="!refreshing" class="empty-state">
        <div class="empty-icon">✓</div>
        <strong>没有匹配的真实记录</strong
        ><span>调整筛选条件，或等待任务和管理操作产生新记录。</span>
      </div>
    </section>

    <div v-if="selected" class="panel-backdrop" @click.self="selected = null">
      <aside class="log-drawer">
        <header>
          <div>
            <h2>记录详情</h2>
            <p>{{ formatTime(selected.time) }} · {{ selected.service }}</p>
          </div>
          <button class="icon-button" @click="selected = null">×</button>
        </header>
        <div class="log-detail-summary">
          <span class="log-level" :class="selected.level">{{
            selected.level
          }}</span
          ><strong>{{ selected.summary }}</strong>
        </div>
        <dl>
          <template v-for="(value, key) in selected.details" :key="key"
            ><dt>{{ key }}</dt>
            <dd>
              <pre>{{ formatDetail(value) }}</pre>
            </dd></template
          >
        </dl>
        <footer>
          <button
            v-if="selected.jobId"
            class="secondary"
            @click="copy(selected.jobId)"
          >
            复制任务 ID</button
          ><button
            v-if="selected.requestId"
            class="secondary"
            @click="copy(selected.requestId)"
          >
            复制请求 ID</button
          ><button class="primary" @click="openGrafana(selected)">
            在 Grafana 深入分析
          </button>
        </footer>
      </aside>
    </div>
  </div>
</template>
