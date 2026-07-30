import { assetDeliveryPolicy } from "../src/assetPresentation";
import type { AssetJobInfo } from "../src/types";

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
