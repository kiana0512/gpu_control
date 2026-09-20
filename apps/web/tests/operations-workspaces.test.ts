import { flushPromises, shallowMount } from "@vue/test-utils";
import type { NodeInfo } from "../src/types";
import CodexRuntime from "../src/views/CodexRuntime.vue";
import Realesrgan from "../src/views/Realesrgan.vue";

const mocks = vi.hoisted(() => ({
  nodes: vi.fn(),
  assetProcessing: vi.fn(),
  realesrgan: vi.fn(),
  refresh: null as null | (() => Promise<void>),
}));

vi.mock("../src/api", () => ({
  api: {
    nodes: mocks.nodes,
    assetProcessing: mocks.assetProcessing,
    realesrgan: mocks.realesrgan,
  },
}));

vi.mock("../src/composables/useAutoRefresh", async () => {
  const { ref } = await import("vue");
  return {
    useAutoRefresh: (refresh: () => Promise<void>) => {
      mocks.refresh = refresh;
      return {
        run: refresh,
        refreshing: ref(false),
        lastUpdatedAt: ref<Date | null>(null),
      };
    },
  };
});

function codexNode(
  id: string,
  authStatus: string,
  errorCode: string | null,
): NodeInfo {
  const now = new Date().toISOString();
  return {
    id,
    display_name: id === "worker-broken" ? "Broken Runtime" : "Healthy Runtime",
    base_url: "http://node",
    pool: "PRIMARY",
    mode: "ACTIVE",
    health: "ONLINE",
    current_jobs: 0,
    max_concurrency: 1,
    gpu_util_percent: 0,
    gpu_temperature_c: 40,
    gpu_power_w: 20,
    free_vram_mb: 20_000,
    total_vram_mb: 24_576,
    manual_reserved: false,
    foreign_queue_detected: false,
    codex_cli: {
      health: errorCode ? "DEGRADED" : "HEALTHY",
      host_entry_installed: true,
      host_version: "codex-host",
      runtime_version: "codex-worker",
      auth_status: authStatus,
      probe_status: errorCode ? "FAILED" : "HEALTHY",
      probe_latency_ms: errorCode ? null : 120,
      last_checked_at: now,
      last_success_at: errorCode ? null : now,
      worker_status: "ONLINE",
      worker_last_heartbeat_at: now,
      heartbeat_fresh: true,
      probe_fresh: true,
      eligibility_reason: errorCode ? "CODEX_AUTH_NOT_READY" : "ELIGIBLE",
      error_code: errorCode,
      task: null,
      scheduler_eligible: !errorCode,
    },
  };
}

describe("operations workspaces", () => {
  beforeEach(() => {
    mocks.nodes.mockReset();
    mocks.assetProcessing.mockReset();
    mocks.realesrgan.mockReset();
    mocks.refresh = null;
  });

  it("prioritizes an unhealthy Codex runtime and supports inspector selection", async () => {
    const cloudInferenceNode = codexNode(
      "autodl-5090-01",
      "MISSING",
      "AUTH_MISSING",
    );
    cloudInferenceNode.display_name = "AutoDL RTX PRO 6000";
    cloudInferenceNode.labels = {
      provider: "autodl",
      codex_runtime_enabled: false,
    };
    mocks.nodes.mockResolvedValue([
      codexNode("worker-healthy", "AUTHENTICATED", null),
      codexNode("worker-broken", "EXPIRED", "REAUTH_REQUIRED"),
      cloudInferenceNode,
    ]);
    mocks.assetProcessing.mockResolvedValue({ workers: [], jobs: [] });
    const wrapper = shallowMount(CodexRuntime);

    await mocks.refresh?.();
    await flushPromises();

    expect(wrapper.find(".attention-band").text()).toContain("Broken Runtime");
    expect(wrapper.text()).not.toContain("AutoDL RTX PRO 6000");
    expect(wrapper.find(".inline-metrics").text()).toContain("1/2");
    expect(wrapper.find(".runtime-inspector").text()).toContain(
      "Broken Runtime",
    );
    expect(wrapper.find(".runtime-inspector").text()).toContain("授权已失效");

    const healthyRow = wrapper
      .findAll(".runtime-row")
      .find((row) => row.text().includes("Healthy Runtime"));
    await healthyRow?.trigger("click");
    expect(wrapper.find(".runtime-inspector").text()).toContain(
      "Healthy Runtime",
    );
    expect(wrapper.find(".runtime-inspector").text()).toContain("真实调用正常");
  });

  it("surfaces failed Real-ESRGAN workers and filters the task ledger", async () => {
    mocks.realesrgan.mockResolvedValue({
      status: "DEGRADED",
      model: "RealESRGAN_x4plus_anime_6B",
      model_sha256: "a".repeat(64),
      image_version: "realesrgan-test",
      ready_nodes: 1,
      total_nodes: 2,
      queue_depth: 1,
      max_queue: 64,
      capacity: 2,
      api: {
        enhance: "/api/v1/realesrgan/enhance?strength=0.7",
        ready: "/api/v1/realesrgan/ready",
        capacity: "/api/v1/realesrgan/capacity",
        content_type: "image/png",
        authentication: "X-API-Key",
        idempotency: "Idempotency-Key",
      },
      nodes: [
        {
          id: "healthy-gpu",
          name: "Healthy GPU",
          ready: true,
          active: 1,
          capacity: 1,
          device: "RTX 4090",
          pytorch: "2.8",
          cuda_runtime: "12.8",
          python: "3.11",
          precision: "fp16",
          tile: 256,
          tile_pad: 16,
          vram_free_mb: 20_000,
          vram_total_mb: 24_576,
          last_metrics: {},
          last_error: null,
        },
        {
          id: "broken-gpu",
          name: "Broken GPU",
          ready: false,
          active: 0,
          capacity: 1,
          device: "RTX 4090",
          pytorch: "2.8",
          cuda_runtime: "12.8",
          python: "3.11",
          precision: "fp16",
          tile: 256,
          tile_pad: 16,
          vram_free_mb: 20_000,
          vram_total_mb: 24_576,
          last_metrics: {},
          last_error: "CUDA context unavailable",
        },
      ],
      tasks: [
        {
          request_id: "active-request",
          status: "RUNNING",
          created_at: "2026-09-20T01:00:00Z",
          finished_at: null,
          node: "healthy-gpu",
          strength: 0.7,
          width: 1024,
          height: 1024,
          input_sha256: "b".repeat(64),
          output_sha256: null,
          queue_ms: 10,
          processing_ms: null,
          cache: "MISS",
          error_code: null,
        },
        {
          request_id: "finished-request",
          status: "SUCCEEDED",
          created_at: "2026-09-20T00:00:00Z",
          finished_at: "2026-09-20T00:00:01Z",
          node: "healthy-gpu",
          strength: 0.7,
          width: 512,
          height: 512,
          input_sha256: "c".repeat(64),
          output_sha256: "d".repeat(64),
          queue_ms: 4,
          processing_ms: 900,
          cache: "HIT",
          error_code: null,
        },
      ],
    });
    const wrapper = shallowMount(Realesrgan);

    await mocks.refresh?.();
    await flushPromises();

    expect(wrapper.find(".issue-rail").text()).toContain("Broken GPU");
    expect(wrapper.find(".worker-inspector").text()).toContain(
      "CUDA context unavailable",
    );
    expect(wrapper.findAll("tbody tr")).toHaveLength(2);

    const activeFilter = wrapper
      .findAll(".scope-switch button")
      .find((button) => button.text().includes("活动 1"));
    await activeFilter?.trigger("click");
    expect(wrapper.findAll("tbody tr")).toHaveLength(1);
    expect(wrapper.find("tbody").text()).toContain("active-request");
    expect(wrapper.find("tbody").text()).not.toContain("finished-request");
  });
});
