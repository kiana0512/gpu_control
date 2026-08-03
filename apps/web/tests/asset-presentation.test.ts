import {
  assetDeliveryPolicy,
  assetExecutionTarget,
  assetProgressMessage,
  assetWorkerState,
  substanceGpuStateSummary,
} from "../src/assetPresentation";
import type {
  AssetJobInfo,
  AssetProcessingOverview,
  AssetWorkerInfo,
} from "../src/types";

function assetJob(overrides: Partial<AssetJobInfo> = {}): AssetJobInfo {
  return {
    job_id: "asset-job-1",
    external_asset_id: "loadtest:synthetic:retopology:1",
    client_id: "test-client",
    job_type: "RETOPOLOGY_PROCESS_V1",
    status: "SUCCEEDED",
    source_filename: "process.blend",
    input_sha256: "a".repeat(64),
    options: {},
    worker_id: "worker-3090-a",
    progress: 100,
    stage: "SUCCEEDED",
    stage_message: "交付完成",
    timing: {
      elapsed_seconds: 10,
      estimated_remaining_seconds: 0,
      last_progress_at: "2026-07-30T00:00:10Z",
    },
    attempt_count: 1,
    error: null,
    created_at: "2026-07-30T00:00:00Z",
    started_at: "2026-07-30T00:00:01Z",
    finished_at: "2026-07-30T00:00:10Z",
    delivery_ready: true,
    review_required: false,
    artifacts_role: "delivery",
    artifacts: [],
    ...overrides,
  };
}

describe("asset delivery policy", () => {
  it("does not claim QA passed for an advisory delivery with a warning", () => {
    const policy = assetDeliveryPolicy(
      assetJob({
        options: {
          qa_warning: {
            enforcement: "advisory",
            failures: ["QUAD_RATIO_BELOW_0.8"],
          },
        },
      }),
    );

    expect(policy).toEqual({
      label: "已交付 · QA 告警",
      className: "warning",
    });
  });

  it("keeps the clean delivery and diagnostic labels distinct", () => {
    expect(assetDeliveryPolicy(assetJob())).toEqual({
      label: "质量检查通过",
      className: "ready",
    });
    expect(
      assetDeliveryPolicy(
        assetJob({
          status: "FAILED",
          delivery_ready: false,
          artifacts_role: "diagnostic",
        }),
      ),
    ).toEqual({ label: "质量检查未通过", className: "blocked" });
  });
});

describe("Substance physical GPU presentation", () => {
  it("shows a real 3090-B next-turn reservation instead of an unassigned worker", () => {
    const job = assetJob({
      job_type: "SUBSTANCE_BAKE_V1",
      status: "QUEUED",
      worker_id: null,
      stage_message: "任务已进入资产处理队列",
      resource_wait: {
        code: "WAITING_FOR_COMFYUI_FRAME",
        message:
          "已获 3090-B 下一轮优先权，等待当前 ComfyUI 帧安全结束后切换烘焙",
        node_id: "worker-3090-b",
        reservation_active: true,
        fence_active: false,
        comfyui_current_jobs: 1,
      },
    });

    expect(assetExecutionTarget(job)).toBe("3090-B · 下一轮已预约");
    expect(assetProgressMessage(job)).toContain("当前 ComfyUI 帧安全结束");
  });

  it("labels Baker process slots without implying four independent GPUs", () => {
    const worker = {
      id: "asset-worker-3090-b-windows",
      status: "ONLINE",
      current_jobs: 0,
      max_concurrency: 4,
    } as AssetWorkerInfo;

    expect(assetWorkerState(worker)).toBe("Baker 进程 0 / 4 使用中");
  });

  it("does not claim a next-turn switch while the shared GPU is blocked", () => {
    const gpu = {
      node_id: "worker-3090-b",
      health: "ONLINE",
      mode: "DRAINING",
      sharing_policy: "exclusive_turn_with_comfyui",
      queue_policy: "production_bake_next_turn_priority",
      comfyui_current_jobs: 1,
      reserved_job_ids: ["bake-1"],
      reservation_expires_at: "2026-08-03T15:00:00Z",
      active_bake_job_ids: [],
      recovery_required: true,
      manual_reserved: false,
      external_busy: false,
      foreign_queue_detected: false,
      free_vram_mb: 12000,
      total_vram_mb: 24576,
    } satisfies NonNullable<AssetProcessingOverview["substance_gpu"]>;

    expect(substanceGpuStateSummary(gpu)).toBe("3090-B 正在安全恢复闭锁");
    expect(
      substanceGpuStateSummary({
        ...gpu,
        recovery_required: false,
        health: "OFFLINE",
      }),
    ).toBe("3090-B 当前离线");
    expect(
      substanceGpuStateSummary({
        ...gpu,
        recovery_required: false,
        reserved_job_ids: [],
        manual_reserved: true,
      }),
    ).toBe("3090-B 当前由管理员保留");
  });
});
