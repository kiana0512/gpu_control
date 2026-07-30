import {
  endToEndDuration,
  formatDuration,
  gpuDuration,
  percentile,
  queueDuration,
  serviceFor,
  type TaskJob,
} from "../src/jobPresentation";

function task(overrides: Partial<TaskJob> = {}): TaskJob {
  return {
    kind: "batch",
    job_id: "batch-1",
    external_batch_id: "assetclaw:shot-1:matting:g1",
    status: "SUCCEEDED",
    workflow_key: "imageclip-rgba",
    workflow_version: "2026.07.30-691770c-r1",
    priority: "batch",
    node_id: null,
    prompt_id: null,
    progress: 100,
    attempt: 1,
    created_at: "2026-07-30T00:00:00Z",
    queued_at: "2026-07-30T00:00:02Z",
    started_at: "2026-07-30T00:00:05Z",
    execution_finished_at: "2026-07-30T00:00:15Z",
    finished_at: "2026-07-30T00:00:20Z",
    error: null,
    ...overrides,
  };
}

describe("job presentation evidence rules", () => {
  it("classifies ImageClip parent batches by their public API", () => {
    expect(serviceFor(task())).toMatchObject({
      key: "imageclip-batch",
      api: "/api/v1/batches/imageclip-rgba",
    });
  });

  it("derives durations only from valid reported endpoints", () => {
    const value = task();
    expect(queueDuration(value)).toBe(3_000);
    expect(gpuDuration(value)).toBe(10_000);
    expect(endToEndDuration(value)).toBe(20_000);
  });

  it("does not invent a duration when an endpoint is missing", () => {
    const value = task({ started_at: null, finished_at: null });
    expect(queueDuration(value)).toBeNull();
    expect(gpuDuration(value)).toBeNull();
    expect(endToEndDuration(value)).toBeNull();
    expect(formatDuration(endToEndDuration(value))).toBe("未上报");
  });

  it("uses server performance values without treating zero as missing", () => {
    const value = task({
      queued_at: null,
      started_at: null,
      execution_finished_at: null,
      performance: { queue_ms: 0, execution_ms: 12_345 },
    });
    expect(queueDuration(value)).toBe(0);
    expect(gpuDuration(value)).toBe(12_345);
  });

  it("computes the nearest-rank percentile from observed values only", () => {
    expect(percentile([null, 1, 2, 3, 4, 5], 0.9)).toBe(5);
    expect(percentile([null, undefined], 0.9)).toBeNull();
  });
});
