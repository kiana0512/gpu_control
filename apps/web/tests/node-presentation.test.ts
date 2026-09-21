import {
  controlPlaneComfyUiUrl,
  formatGpuPower,
  formatGpuTemperature,
  nextCloudComfyUiInventory,
  resolveComfyUiAccessUrl,
  resolveCloudInstanceComfyUiAccessUrl,
} from "../src/nodePresentation";
import type {
  CloudInstance,
  CloudServerOverview,
  NodeInfo,
} from "../src/types";

function node(overrides: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id: "worker-3090-a",
    display_name: "3090-A",
    base_url: "http://10.3.34.12:8188",
    pool: "PRIMARY",
    mode: "ACTIVE",
    health: "ONLINE",
    current_jobs: 1,
    max_concurrency: 1,
    gpu_util_percent: 100,
    gpu_temperature_c: 68,
    gpu_power_w: 301.4,
    free_vram_mb: 12000,
    total_vram_mb: 24576,
    manual_reserved: false,
    foreign_queue_detected: false,
    ...overrides,
  };
}

function cloudInstance(
  nodeId: string | null,
  serviceUrl: string | null,
  overrides: Partial<CloudInstance> = {},
): CloudInstance {
  return {
    ref: "app:pro-example",
    product: "app",
    instance_id: "pro-example",
    name: "Cloud GPU",
    state: "running",
    provider_status: "running",
    region: "CN",
    gpu_spec: "RTX 5090",
    gpu_count: 1,
    charge_type: "payg",
    price_yuan_per_hour: 1,
    disk_total_gib: 100,
    disk_used_gib: 20,
    disk_used_percent: 20,
    cpu_percent: 10,
    memory_percent: 10,
    application: { name: "ComfyUI", version: "1" },
    access: {
      jupyter: null,
      service_6006: serviceUrl,
      service_6008: null,
      ssh: null,
    },
    created_at: "2026-09-18T00:00:00Z",
    started_at: "2026-09-18T00:00:00Z",
    timed_shutdown_at: null,
    scheduled_start_at: null,
    scheduled_stop_at: null,
    snapshot_error: null,
    management: {
      managed: true,
      scheduling_enabled: true,
      desired_state: "running",
      node_id: nodeId,
      operation: null,
    },
    ...overrides,
  };
}

function cloudOverview(
  instances: CloudInstance[],
  overrides: Partial<CloudServerOverview> = {},
): CloudServerOverview {
  return {
    configured: true,
    provider: "AutoDL",
    synced_at: "2026-09-18T00:00:00Z",
    balance: {
      available_yuan: 1,
      voucher_yuan: 0,
      accumulated_yuan: 0,
    },
    summary: {
      total: instances.length,
      running: instances.length,
      transitioning: 0,
      stopped: 0,
      error: 0,
    },
    instances,
    ...overrides,
  };
}

describe("GPU node telemetry presentation", () => {
  it("formats live temperature and power with compact units", () => {
    const value = node();
    expect(formatGpuTemperature(value)).toBe("68 °C");
    expect(formatGpuPower(value)).toBe("301 W");
  });

  it("does not present missing, invalid, or offline values as live telemetry", () => {
    expect(formatGpuTemperature(node({ gpu_temperature_c: null }))).toBe("—");
    expect(formatGpuPower(node({ gpu_power_w: Number.NaN }))).toBe("—");
    expect(formatGpuTemperature(node({ health: "OFFLINE" }))).toBe("—");
  });
});

describe("ComfyUI browser access", () => {
  it("maps a cloud node to its official HTTPS service by management node ID", () => {
    const target = node({
      id: "cloud-node-01",
      base_url: "http://internal-tunnel:16006",
    });
    const instances = [
      cloudInstance(
        "another-node",
        "https://other-node.autodl.com/service/6006",
      ),
      cloudInstance(
        target.id,
        "https://public-entry.autodl.com/service/6006?token=opaque",
      ),
    ];

    expect(
      resolveComfyUiAccessUrl(target, instances, "control.example.com"),
    ).toBe("https://public-entry.autodl.com/service/6006?token=opaque");
  });

  it("adds a validated durable workspace fragment when AutoDL omits it", () => {
    const target = node({
      id: "autodl-5090-01",
      base_url: "http://autodl-5090-tunnel:16006",
      labels: {
        provider: "autodl",
        comfyui_browser_proxy_port: 16006,
        comfyui_browser_fragment: "ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
      },
    });

    expect(
      resolveComfyUiAccessUrl(
        target,
        [
          cloudInstance(
            target.id,
            "https://u765793-7894be501780.westd.seetacloud.com:8443/",
          ),
        ],
        "control.example.com",
      ),
    ).toBe(
      "https://u765793-7894be501780.westd.seetacloud.com:8443/#ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
    );
  });

  it("builds the managed cloud ComfyUI URL without exposing provider credentials", () => {
    expect(
      resolveCloudInstanceComfyUiAccessUrl(
        cloudInstance(
          "autodl-5090-01",
          "https://u765793-7894be501780.westd.seetacloud.com:8443/#ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
        ),
        "10.3.34.11",
      ),
    ).toBe(
      "https://u765793-7894be501780.westd.seetacloud.com:8443/#ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
    );
    expect(
      controlPlaneComfyUiUrl("10.3.34.11", "javascript:alert(1)"),
    ).toBeNull();
  });

  it("rejects malformed node fragments and preserves the safe provider URL", () => {
    const target = node({
      id: "autodl-5090-01",
      labels: {
        provider: "autodl",
        comfyui_browser_fragment: "javascript:alert(1)",
      },
    });
    expect(
      resolveComfyUiAccessUrl(
        target,
        [cloudInstance(target.id, "https://public-entry.autodl.com/")],
        "control.example.com",
      ),
    ).toBe("https://public-entry.autodl.com/");
  });

  it("fails closed for unsafe or ambiguous cloud access URLs", () => {
    const target = node({
      id: "cloud-node-01",
      base_url: "http://internal-tunnel:16006",
    });
    expect(
      resolveComfyUiAccessUrl(
        target,
        [cloudInstance(target.id, "http://public-entry.autodl.com:6006")],
        "control.example.com",
      ),
    ).toBeNull();
    expect(
      resolveComfyUiAccessUrl(
        target,
        [cloudInstance(target.id, "https://attacker.example.com/6006")],
        "control.example.com",
      ),
    ).toBeNull();
    expect(
      resolveComfyUiAccessUrl(
        target,
        [
          cloudInstance(target.id, "https://one.autodl.com/6006"),
          cloudInstance(target.id, "https://two.autodl.com/6006", {
            ref: "app:pro-other",
            instance_id: "pro-other",
          }),
        ],
        "control.example.com",
      ),
    ).toBeNull();
  });

  it("preserves the existing local-node URL behavior", () => {
    expect(resolveComfyUiAccessUrl(node(), [], "control.example.com")).toBe(
      "http://10.3.34.12:8188/",
    );
    expect(
      resolveComfyUiAccessUrl(
        node({
          id: "control-4090",
          base_url: "http://comfyui-4090:8188",
        }),
        [],
        "control.example.com",
      ),
    ).toBe(
      "http://control.example.com:8188/#551d82b0-b1fb-483a-a5ea-564bdb813625",
    );
  });

  it("never falls back to a Docker data-plane URL for a known cloud node", () => {
    const byLabel = node({
      id: "cloud-node-01",
      base_url: "http://internal-tunnel:16006",
      labels: { provider: "autodl" },
    });
    expect(
      resolveComfyUiAccessUrl(byLabel, [], "control.example.com"),
    ).toBeNull();

    const remembered = node({
      id: "cloud-node-02",
      base_url: "http://another-internal-tunnel:16006",
    });
    expect(
      resolveComfyUiAccessUrl(
        remembered,
        [],
        "control.example.com",
        new Set([remembered.id]),
      ),
    ).toBeNull();
  });

  it("keeps the last known-good inventory across partial and empty snapshots", () => {
    const lastKnownGood = [
      cloudInstance(
        "cloud-node-01",
        "https://public-entry.autodl.com/service/6006",
      ),
    ];
    const partial = cloudOverview([], {
      partial_errors: [{ scope: "app", code: "AUTODL_READ_FAILED" }],
    });

    expect(nextCloudComfyUiInventory(lastKnownGood, partial)).toEqual(
      lastKnownGood,
    );
    expect(nextCloudComfyUiInventory(lastKnownGood, cloudOverview([]))).toEqual(
      lastKnownGood,
    );
    expect(
      nextCloudComfyUiInventory(
        lastKnownGood,
        cloudOverview([], { configured: false }),
      ),
    ).toEqual(lastKnownGood);
  });

  it("fails closed when a complete inventory drops a remembered binding", () => {
    const target = node({
      id: "cloud-node-01",
      base_url: "http://internal-tunnel:16006",
    });
    const replacement = nextCloudComfyUiInventory(
      [
        cloudInstance(
          target.id,
          "https://public-entry.autodl.com/service/6006",
        ),
      ],
      cloudOverview([
        cloudInstance(
          "another-node",
          "https://other-node.autodl.com/service/6006",
        ),
      ]),
    );

    expect(
      resolveComfyUiAccessUrl(
        target,
        replacement,
        "control.example.com",
        new Set([target.id]),
      ),
    ).toBeNull();
  });
});

it("naturally orders dynamically registered nodes without a fixed worker list", async () => {
  const { compareNodes } = await import("../src/nodePresentation");
  const nodes = [
    "worker-6000-10",
    "worker-5070ti-01",
    "control-4090",
    "worker-6000-2",
  ].map((id) => ({ id }));
  expect(nodes.sort(compareNodes).map((node) => node.id)).toEqual([
    "control-4090",
    "worker-5070ti-01",
    "worker-6000-2",
    "worker-6000-10",
  ]);
});

it("shows registered validation profiles without making an admission decision", async () => {
  const { validatedVramSummary } = await import("../src/nodePresentation");
  const item = {
    id: "worker-new",
    labels: {
      validated_vram_profiles: {
        modelview: {
          status: "PASSED",
          node_id: "worker-new",
          version: "1.0",
          min_vram_mb: 16000,
        },
      },
    },
  } as Pick<NodeInfo, "id" | "labels">;
  expect(validatedVramSummary(item)).toContain(
    "专项验收 1 个工作流版本 / 16 GB",
  );
  expect(validatedVramSummary(item)).toContain("以后端兼容性校验为准");
  item.id = "other-node";
  expect(validatedVramSummary(item)).toBeNull();
});
