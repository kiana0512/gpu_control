import { flushPromises, shallowMount } from "@vue/test-utils";
import type { NodeInfo } from "../src/types";
import Nodes from "../src/views/Nodes.vue";

const mocks = vi.hoisted(() => ({
  nodes: vi.fn(),
  cloudServers: vi.fn(),
  refresh: null as null | (() => Promise<void>),
}));

vi.mock("../src/api", () => ({
  api: {
    nodes: mocks.nodes,
    cloudServers: mocks.cloudServers,
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

function localNode(): NodeInfo {
  return {
    id: "worker-local-01",
    display_name: "Local GPU",
    base_url: "http://10.3.34.12:8188",
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
  };
}

describe("Nodes cloud access refresh isolation", () => {
  it("renders local nodes when cloud inventory fails and throttles automatic cloud refreshes", async () => {
    mocks.nodes.mockResolvedValue([localNode()]);
    mocks.cloudServers.mockRejectedValue(new Error("provider unavailable"));
    const wrapper = shallowMount(Nodes);

    expect(mocks.refresh).not.toBeNull();
    await mocks.refresh?.();
    await flushPromises();

    expect(wrapper.text()).toContain("Local GPU");
    expect(wrapper.text()).not.toContain("provider unavailable");
    expect(mocks.cloudServers).toHaveBeenCalledTimes(1);

    await mocks.refresh?.();
    await flushPromises();
    expect(mocks.nodes).toHaveBeenCalledTimes(2);
    expect(mocks.cloudServers).toHaveBeenCalledTimes(1);
  });
});
