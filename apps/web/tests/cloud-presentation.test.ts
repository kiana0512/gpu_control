import {
  canChangeCloudPower,
  cloudOperationDuration,
  cloudOperationLabel,
  cloudSchedulingSummary,
  cloudThermalState,
  effectiveCloudState,
  filterCloudInstances,
  formatCloudScheduleInput,
  formatCloudTimestamp,
  validateCloudSchedule,
} from "../src/cloudPresentation";
import type { CloudInstance } from "../src/types";

function instance(overrides: Partial<CloudInstance> = {}): CloudInstance {
  return {
    ref: "app:pro-a1",
    product: "app",
    instance_id: "pro-a1",
    name: "渲染节点 1",
    state: "stopped",
    provider_status: "shutdown",
    region: "西北 A 区",
    gpu_spec: "RTX 4090",
    gpu_count: 1,
    charge_type: "payg",
    price_yuan_per_hour: 1.5,
    disk_total_gib: 50,
    disk_used_gib: 10,
    disk_used_percent: 20,
    cpu_percent: null,
    memory_percent: null,
    application: { name: "ComfyUI", version: "1" },
    access: {
      jupyter: null,
      service_6006: null,
      service_6008: null,
      ssh: null,
    },
    created_at: "2026-09-18T00:00:00Z",
    started_at: null,
    timed_shutdown_at: null,
    scheduled_start_at: null,
    scheduled_stop_at: null,
    snapshot_error: null,
    management: {
      managed: true,
      scheduling_enabled: true,
      desired_state: "running",
      node_id: "worker-cloud-01",
      operation: null,
    },
    ...overrides,
  };
}

describe("cloud server presentation", () => {
  it("places durable operations above a stale provider snapshot", () => {
    const value = instance({
      management: {
        managed: true,
        scheduling_enabled: true,
        desired_state: "running",
        node_id: "worker-cloud-01",
        operation: { id: "op-1", status: "WAITING", desired_state: "running" },
      },
    });
    expect(effectiveCloudState(value)).toBe("starting");
    expect(cloudThermalState(value)).toBe("冷启动中");
    expect(canChangeCloudPower(value, "running")).toBe(false);
    expect(cloudOperationLabel("WAITING", "running")).toBe(
      "开机 · 等待状态收敛",
    );
    expect(
      effectiveCloudState(value, {
        id: "op-1",
        status: "CONFIRMED",
        desired_state: "running",
      }),
    ).toBe("running");
  });

  it("uses durable operation timestamps for phase timing", () => {
    const operation = {
      id: "op-1",
      operation_id: "op-1",
      status: "WAITING" as const,
      desired_state: "running" as const,
      created_at: "2026-09-18T00:00:00Z",
      started_at: "2026-09-18T00:00:02Z",
      dispatch_attempted_at: "2026-09-18T00:00:03Z",
      dispatch_ack_at: "2026-09-18T00:00:05Z",
    };
    expect(
      cloudOperationDuration(operation, Date.parse("2026-09-18T00:01:10Z")),
    ).toBe("等待状态收敛 1 分 5 秒");
    expect(
      cloudOperationDuration({ ...operation, dispatch_ack_at: null }),
    ).toMatch(/^等待状态收敛 /);
  });

  it("explains scheduling eligibility without claiming a task assignment", () => {
    expect(
      cloudSchedulingSummary(instance({ state: "running" })),
    ).toMatchObject({ label: "可参与接单", tone: "healthy" });
    expect(
      cloudSchedulingSummary(
        instance({
          state: "running",
          management: {
            managed: true,
            scheduling_enabled: false,
            desired_state: null,
            node_id: "worker-cloud-01",
            operation: null,
          },
        }),
      ).reason,
    ).toContain("尚未启用");
  });

  it("filters by issues, product and search while keeping urgent states first", () => {
    const rows = [
      instance({ ref: "app:pro-a2", instance_id: "pro-a2", name: "冷节点" }),
      instance({
        ref: "pro:pro-b1",
        product: "pro",
        instance_id: "pro-b1",
        name: "异常节点",
        state: "unknown",
      }),
      instance({
        ref: "app:pro-a3",
        instance_id: "pro-a3",
        name: "运行节点",
        state: "running",
      }),
    ];
    expect(filterCloudInstances(rows, "issues", "all", "")).toHaveLength(1);
    expect(
      filterCloudInstances(rows, "all", "app", "运行")[0]?.instance_id,
    ).toBe("pro-a3");
    expect(
      filterCloudInstances(rows, "all", "all", "").map((row) => row.state),
    ).toEqual(["unknown", "running", "stopped"]);
  });

  it("accepts provider epoch seconds and rejects invalid timestamps", () => {
    expect(formatCloudTimestamp(1_757_635_200)).not.toBe("—");
    expect(formatCloudTimestamp("1757635200")).not.toBe("—");
    expect(formatCloudTimestamp("not-a-date")).toBe("—");
  });

  it("validates one-off schedules in local time and serializes them as ISO", () => {
    const now = Date.parse("2026-09-18T00:00:00Z");
    const start = formatCloudScheduleInput("2026-09-18T02:00:00Z");
    const stop = formatCloudScheduleInput("2026-09-18T04:00:00Z");
    const result = validateCloudSchedule(start, stop, now);
    expect(result.valid).toBe(true);
    if (result.valid) {
      expect(Date.parse(result.scheduled_start_at ?? "")).toBeGreaterThan(now);
      expect(Date.parse(result.scheduled_stop_at ?? "")).toBeGreaterThan(
        Date.parse(result.scheduled_start_at ?? ""),
      );
    }
  });

  it("rejects past or reversed schedules while allowing an explicit clear", () => {
    const now = Date.parse("2026-09-18T00:00:00Z");
    expect(validateCloudSchedule("2000-01-01T00:00", "", now)).toMatchObject({
      valid: false,
      error: "定时开机时间必须至少晚于当前时间 1 分钟",
    });
    expect(validateCloudSchedule("2027-09-19T00:00", "", now)).toMatchObject({
      valid: false,
      error: "定时开机时间不能超过未来 365 天",
    });
    expect(
      validateCloudSchedule("2099-01-02T00:00", "2099-01-01T00:00", now),
    ).toMatchObject({ valid: false });
    expect(validateCloudSchedule("", "", now)).toEqual({
      valid: true,
      scheduled_start_at: null,
      scheduled_stop_at: null,
    });
  });
});
