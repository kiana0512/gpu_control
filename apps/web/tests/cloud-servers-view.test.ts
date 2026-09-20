import { flushPromises, shallowMount } from "@vue/test-utils";
import type { CloudInstance, CloudServerOverview } from "../src/types";
import CloudServers from "../src/views/CloudServers.vue";

const mocks = vi.hoisted(() => ({
  cloudServers: vi.fn(),
  refresh: null as null | (() => Promise<void>),
  run: null as null | (() => Promise<void>),
  role: "viewer",
}));

vi.mock("../src/api", () => ({
  api: {
    cloudServers: mocks.cloudServers,
  },
  session: {
    role: () => mocks.role,
  },
}));

vi.mock("../src/composables/useAutoRefresh", async () => {
  const { ref } = await import("vue");
  return {
    useAutoRefresh: (refresh: () => Promise<void>) => {
      const refreshing = ref(false);
      mocks.refresh = refresh;
      mocks.run = async () => {
        if (refreshing.value) return;
        refreshing.value = true;
        try {
          await refresh();
        } finally {
          refreshing.value = false;
        }
      };
      return {
        run: mocks.run,
        refreshing,
        lastUpdatedAt: ref<Date | null>(null),
      };
    },
  };
});

function instance(
  name: string,
  overrides: Partial<CloudInstance> = {},
): CloudInstance {
  return {
    ref: `app:${name}`,
    product: "app",
    instance_id: name,
    name,
    state: "running",
    provider_status: "running",
    region: "西北 A 区",
    gpu_spec: "RTX 5090",
    gpu_count: 1,
    charge_type: "payg",
    price_yuan_per_hour: 2,
    disk_total_gib: 50,
    disk_used_gib: 10,
    disk_used_percent: 20,
    cpu_percent: 10,
    memory_percent: 20,
    application: { name: "ComfyUI", version: "1" },
    access: {
      jupyter: null,
      service_6006: null,
      service_6008: null,
      ssh: null,
    },
    created_at: "2026-09-18T00:00:00Z",
    started_at: "2026-09-18T00:01:00Z",
    timed_shutdown_at: null,
    scheduled_start_at: null,
    scheduled_stop_at: null,
    snapshot_error: null,
    management: {
      managed: true,
      scheduling_enabled: true,
      desired_state: "running",
      node_id: `worker-${name}`,
      operation: null,
    },
    ...overrides,
  };
}

function overview(
  instances: CloudInstance[],
  partialErrors: CloudServerOverview["partial_errors"] = [],
): CloudServerOverview {
  return {
    configured: true,
    provider: "AutoDL",
    synced_at: "2026-09-20T00:00:00Z",
    balance: {
      available_yuan: 100,
      voucher_yuan: 0,
      accumulated_yuan: 20,
    },
    summary: {
      total: instances.length,
      running: instances.length,
      transitioning: 0,
      stopped: 0,
      error: 0,
    },
    partial_errors: partialErrors,
    instances,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("CloudServers inventory refresh", () => {
  beforeEach(() => {
    mocks.cloudServers.mockReset();
    mocks.refresh = null;
    mocks.run = null;
    mocks.role = "viewer";
  });

  it("keeps the last complete inventory when a later snapshot is partial", async () => {
    mocks.cloudServers
      .mockResolvedValueOnce(overview([instance("complete-5090")]))
      .mockResolvedValueOnce(
        overview(
          [instance("partial-only")],
          [{ scope: "app", code: "AUTODL_READ_FAILED" }],
        ),
      );
    const wrapper = shallowMount(CloudServers, {
      global: { stubs: { "el-icon": true } },
    });

    await mocks.refresh?.();
    await flushPromises();
    expect(wrapper.text()).toContain("complete-5090");

    await mocks.refresh?.();
    await flushPromises();
    expect(wrapper.text()).toContain("complete-5090");
    expect(wrapper.text()).not.toContain("partial-only");
    expect(wrapper.text()).toContain("AutoDL 部分数据未同步");
    expect(wrapper.text()).toContain("AUTODL_READ_FAILED");
  });

  it("ignores a stale response that finishes after a newer generation", async () => {
    const oldRequest = deferred<CloudServerOverview>();
    const newRequest = deferred<CloudServerOverview>();
    mocks.cloudServers
      .mockReturnValueOnce(oldRequest.promise)
      .mockReturnValueOnce(newRequest.promise);
    const wrapper = shallowMount(CloudServers, {
      global: { stubs: { "el-icon": true } },
    });

    const oldRefresh = mocks.refresh?.();
    const newRefresh = mocks.refresh?.();
    newRequest.resolve(overview([instance("new-generation")]));
    await newRefresh;
    oldRequest.resolve(overview([instance("stale-generation")]));
    await oldRefresh;
    await flushPromises();

    expect(wrapper.text()).toContain("new-generation");
    expect(wrapper.text()).not.toContain("stale-generation");
  });

  it("queues one forced refresh behind a failing in-flight refresh", async () => {
    const currentRequest = deferred<CloudServerOverview>();
    mocks.cloudServers
      .mockReturnValueOnce(currentRequest.promise)
      .mockResolvedValueOnce(overview([instance("forced-result")]));
    const wrapper = shallowMount(CloudServers, {
      global: { stubs: { "el-icon": true } },
    });

    const currentRefresh = mocks.run?.();
    await flushPromises();
    await wrapper.find(".heading-actions button").trigger("click");
    expect(mocks.cloudServers).toHaveBeenCalledTimes(1);

    currentRequest.reject(new Error("temporary provider failure"));
    await currentRefresh;
    await flushPromises();

    expect(mocks.cloudServers).toHaveBeenCalledTimes(2);
    expect(mocks.cloudServers).toHaveBeenNthCalledWith(1, false);
    expect(mocks.cloudServers).toHaveBeenNthCalledWith(2, true);
    expect(wrapper.text()).toContain("forced-result");
  });

  it("prioritizes running resources and switches to expandable compact rows", async () => {
    mocks.role = "operator";
    mocks.cloudServers.mockResolvedValueOnce(
      overview([
        instance("stopped-instance", {
          state: "stopped",
          provider_status: "shutdown",
          management: {
            managed: true,
            scheduling_enabled: true,
            desired_state: "stopped",
            node_id: "worker-stopped",
            operation: null,
          },
        }),
        instance("running-instance"),
      ]),
    );
    const wrapper = shallowMount(CloudServers, {
      global: { stubs: { "el-icon": true } },
    });
    await mocks.refresh?.();
    await flushPromises();

    const groups = wrapper.findAll(".resource-group");
    expect(groups[0]?.text()).toContain("运行与处理中");
    expect(groups[0]?.text()).toContain("running-instance");
    expect(groups[1]?.text()).toContain("已停止与待用");
    expect(wrapper.findAll(".instance-details")).toHaveLength(2);

    const viewButtons = wrapper.findAll(".view-switcher button");
    await viewButtons[1]?.trigger("click");
    expect(wrapper.find(".resource-workspace").classes()).toContain(
      "view-compact",
    );
    expect(wrapper.findAll(".instance-details")).toHaveLength(0);

    const firstCard = wrapper.find(".instance-card");
    await firstCard.find(".detail-toggle").trigger("click");
    expect(firstCard.find(".instance-details").exists()).toBe(true);

    await firstCard.find('input[type="checkbox"]').setValue(true);
    expect(firstCard.classes()).toContain("selected");
    expect(firstCard.text()).toContain("可关机");
  });

  it("opens the managed cloud ComfyUI through the stable control-plane proxy", async () => {
    const browserOpen = vi.spyOn(window, "open").mockImplementation(() => null);
    mocks.cloudServers.mockResolvedValueOnce(
      overview([
        instance("pro6000", {
          access: {
            jupyter: null,
            service_6006:
              "https://u765793-7894be501780.westd.seetacloud.com:8443/#ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
            service_6008: null,
            ssh: null,
          },
          management: {
            managed: true,
            scheduling_enabled: true,
            desired_state: "running",
            node_id: "autodl-5090-01",
            operation: null,
          },
        }),
      ]),
    );
    const wrapper = shallowMount(CloudServers, {
      global: { stubs: { "el-icon": true } },
    });
    await mocks.refresh?.();
    await flushPromises();

    const button = wrapper
      .findAll("button")
      .find((candidate) => candidate.text().includes("ComfyUI 直连"));
    expect(button).toBeDefined();
    await button?.trigger("click");
    expect(browserOpen).toHaveBeenCalledWith(
      "https://localhost:16006/#ec7efee0-62d2-4ef6-aa39-f13776e3d8a1",
      "_blank",
      "noopener,noreferrer",
    );
    browserOpen.mockRestore();
  });
});
