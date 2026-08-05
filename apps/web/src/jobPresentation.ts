import type { JobInfo } from "./types";

export interface TaskPerformance {
  queue_ms?: number | null;
  execution_ms?: number | null;
  assembly_ms?: number | null;
  artifact_publish_ms?: number | null;
  gpu_service_ms_total?: number | null;
  gpu_service_measurements_complete?: boolean;
  frames_per_gpu_minute?: number | null;
  megapixels_per_gpu_second?: number | null;
  scheduler_restarts?: number | null;
  reassignments?: number | null;
  straggler_ratio?: number | null;
}

export type TaskJob = JobInfo & {
  validated_at?: string | null;
  queued_at?: string | null;
  last_progress_at?: string | null;
  execution_finished_at?: string | null;
  assembling_at?: string | null;
  assembling_started_at?: string | null;
  artifact_ready_at?: string | null;
  updated_at?: string | null;
  pipeline_commit?: string | null;
  pipeline_sha256?: string | null;
  output_node?: string | null;
  performance?: TaskPerformance | null;
};

export interface TaskService {
  key: string;
  label: string;
  shortLabel: string;
  api: string;
}

const SERVICE_BY_WORKFLOW: Record<string, Omit<TaskService, "key">> = {
  "imageclip-rgba": {
    label: "ImageClip RGBA 抠图",
    shortLabel: "抠图",
    api: "/api/v1/services/imageclip-rgba",
  },
  "modelview-inpaint": {
    label: "ModelView 局部重绘",
    shortLabel: "局部重绘",
    api: "/api/v1/services/modelview-inpaint",
  },
  "modelview-roughness": {
    label: "PBR 粗糙度生成",
    shortLabel: "PBR 粗糙度",
    api: "/api/v1/services/modelview-roughness",
  },
  inpaint: {
    label: "通用图像重绘",
    shortLabel: "图像重绘",
    api: "/api/v1/jobs/inpaint",
  },
};

export function serviceFor(job: TaskJob): TaskService {
  if (job.kind === "batch" && job.workflow_key === "imageclip-rgba") {
    return {
      key: "imageclip-batch",
      label: "动画序列帧抠图",
      shortLabel: "序列帧抠图",
      api: "/api/v1/batches/imageclip-rgba",
    };
  }
  const known = SERVICE_BY_WORKFLOW[job.workflow_key];
  if (known) return { key: job.workflow_key, ...known };
  return {
    key: `workflow:${job.workflow_key}`,
    label: "GPU 自定义工作流",
    shortLabel: "自定义工作流",
    api: "/api/v1/jobs",
  };
}

export function taskName(job: TaskJob): string {
  return job.external_batch_id || job.job_id;
}

export function compactTaskName(job: TaskJob): string {
  const value = taskName(job);
  return value.length > 34 ? `${value.slice(0, 30)}…` : value;
}

export function statusGroup(job: TaskJob): string {
  const status = job.status.toUpperCase();
  if (["CREATED", "VALIDATING", "QUEUED", "PENDING"].includes(status))
    return "queued";
  if (["CLAIMED", "RUNNING", "ASSEMBLING", "CANCELLING"].includes(status))
    return "active";
  if (status === "SUCCEEDED") return "succeeded";
  if (status === "PARTIAL_SUCCESS") return "attention";
  if (["FAILED", "TIMED_OUT", "CANCELLED"].includes(status)) return "attention";
  return "other";
}

export function isTerminal(job: TaskJob): boolean {
  return ["SUCCEEDED", "PARTIAL_SUCCESS", "FAILED", "TIMED_OUT", "CANCELLED"].includes(
    job.status.toUpperCase(),
  );
}

export function validTimestamp(
  value: string | null | undefined,
): number | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function durationBetween(
  start: string | null | undefined,
  end: string | null | undefined,
): number | null {
  const started = validTimestamp(start);
  const ended = validTimestamp(end);
  if (started === null || ended === null || ended < started) return null;
  return ended - started;
}

function reportedMilliseconds(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

export function assemblingAt(job: TaskJob): string | null {
  return job.assembling_at ?? job.assembling_started_at ?? null;
}

export function queueDuration(job: TaskJob): number | null {
  return (
    reportedMilliseconds(job.performance?.queue_ms) ??
    durationBetween(job.queued_at, job.started_at)
  );
}

export function gpuDuration(job: TaskJob): number | null {
  return (
    reportedMilliseconds(job.performance?.execution_ms) ??
    durationBetween(job.started_at, job.execution_finished_at)
  );
}

export function assemblyDuration(job: TaskJob): number | null {
  return (
    reportedMilliseconds(job.performance?.assembly_ms) ??
    durationBetween(assemblingAt(job), job.artifact_ready_at)
  );
}

export function publishDuration(job: TaskJob): number | null {
  return (
    reportedMilliseconds(job.performance?.artifact_publish_ms) ??
    durationBetween(job.artifact_ready_at, job.finished_at)
  );
}

export function endToEndDuration(job: TaskJob): number | null {
  return durationBetween(job.created_at, job.finished_at);
}

export function validationDuration(job: TaskJob): number | null {
  return durationBetween(job.created_at, job.validated_at);
}

export function formatDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "未上报";
  const seconds = Math.max(0, Math.round(value / 1000));
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  if (minutes < 60) return `${minutes}分${remainingSeconds}秒`;
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours}小时${remainingMinutes}分`;
}

export function formatDateTime(value: string | null | undefined): string {
  const timestamp = validTimestamp(value);
  if (timestamp === null) return "未上报";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(timestamp);
}

export function median(
  values: Array<number | null | undefined>,
): number | null {
  const observed = values
    .filter((value): value is number => typeof value === "number")
    .filter((value) => Number.isFinite(value) && value >= 0)
    .sort((left, right) => left - right);
  if (!observed.length) return null;
  const middle = Math.floor(observed.length / 2);
  return observed.length % 2
    ? observed[middle]
    : (observed[middle - 1] + observed[middle]) / 2;
}

export function percentile(
  values: Array<number | null | undefined>,
  percent: number,
): number | null {
  const observed = values
    .filter((value): value is number => typeof value === "number")
    .filter((value) => Number.isFinite(value) && value >= 0)
    .sort((left, right) => left - right);
  if (!observed.length) return null;
  const index = Math.max(
    0,
    Math.ceil(observed.length * Math.min(1, Math.max(0, percent))) - 1,
  );
  return observed[index];
}

export function nodeSummary(job: TaskJob): string {
  if (job.kind !== "batch") return job.node_id ?? "未分配";
  const entries = Object.entries(job.node_distribution ?? {});
  return entries.length
    ? entries.map(([node, count]) => `${node} · ${count}`).join(" / ")
    : "未分配";
}

export function taskSearchText(job: TaskJob): string {
  const service = serviceFor(job);
  return [
    job.job_id,
    job.external_batch_id,
    job.workflow_key,
    job.workflow_version,
    job.status,
    job.tenant_id,
    job.node_id,
    service.label,
    service.api,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
}
