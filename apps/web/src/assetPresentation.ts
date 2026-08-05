import type {
  AssetJobInfo,
  AssetProcessingOverview,
  AssetWorkerInfo,
} from "./types";

export interface AssetDeliveryPolicy {
  label: string;
  className: "ready" | "warning" | "blocked";
}

function hasQaWarning(job: AssetJobInfo): boolean {
  const warning = job.options.qa_warning;
  return warning !== undefined && warning !== null;
}

export function assetDeliveryPolicy(job: AssetJobInfo): AssetDeliveryPolicy {
  if (job.delivery_ready) {
    if (job.options.direct_v2_result) {
      return { label: "FBX 已交付 · 等待检查", className: "ready" };
    }
    if (job.status === "SUCCEEDED" && hasQaWarning(job)) {
      return { label: "已交付 · QA 告警", className: "warning" };
    }
    return { label: "质量检查通过", className: "ready" };
  }
  if (job.artifacts_role === "diagnostic") {
    return { label: "质量检查未通过", className: "blocked" };
  }
  return { label: "正在生成交付", className: "blocked" };
}

export function isSubstanceWorker(worker: AssetWorkerInfo): boolean {
  return (
    worker.id === "asset-worker-3090-b-windows" ||
    worker.id.startsWith("asset-worker-3090-b-windows-")
  );
}

export function assetWorkerState(worker: AssetWorkerInfo): string {
  if (worker.status !== "ONLINE") return "心跳离线";
  if (isSubstanceWorker(worker)) {
    return `Baker 进程 ${worker.current_jobs} / ${worker.max_concurrency} 使用中`;
  }
  return `${worker.current_jobs} / ${worker.max_concurrency} CPU 槽位使用中`;
}

export function assetExecutionTarget(job: AssetJobInfo): string {
  if (job.worker_id) return job.worker_id;
  if (job.job_type !== "SUBSTANCE_BAKE_V1") return "尚未分配";
  return job.resource_wait?.reservation_active
    ? "3090-B · 下一轮已预约"
    : "等待 3090-B";
}

export function assetProgressMessage(job: AssetJobInfo): string {
  return job.resource_wait?.message || job.stage_message;
}

export function substanceGpuStateSummary(
  gpu: AssetProcessingOverview["substance_gpu"],
): string {
  if (!gpu) return "等待 3090-B 状态同步";
  if (gpu.recovery_required) return "3090-B 正在安全恢复闭锁";
  if (gpu.health !== "ONLINE") return "3090-B 当前离线";
  if (gpu.active_bake_job_ids.length) {
    return `3090-B 正在烘焙 ${gpu.active_bake_job_ids.length} 个任务`;
  }
  if (gpu.manual_reserved) return "3090-B 当前由管理员保留";
  if (gpu.external_busy) return "3090-B 检测到外部 GPU 活动";
  if (gpu.foreign_queue_detected) return "3090-B 检测到未纳管 ComfyUI 队列";
  if (gpu.reserved_job_ids.length && gpu.comfyui_current_jobs > 0) {
    return `PBR 已获下一轮优先权 · 等待 ${gpu.comfyui_current_jobs} 个 ComfyUI 当前帧结束`;
  }
  if (gpu.reserved_job_ids.length) {
    return `3090-B 已为 ${gpu.reserved_job_ids.length} 个 PBR 任务预约`;
  }
  if (gpu.comfyui_current_jobs > 0) {
    return `3090-B 正在执行 ${gpu.comfyui_current_jobs} 个 ComfyUI 任务`;
  }
  return gpu.mode === "ACTIVE"
    ? "3090-B 当前可切换烘焙"
    : `3090-B 当前为 ${gpu.mode} 模式`;
}
