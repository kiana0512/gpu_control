import type {
  CloudInstance,
  CloudInstanceState,
  CloudOperationStatus,
  CloudOperationView,
} from "./types";

export type CloudFilter =
  | "all"
  | "running"
  | "transitioning"
  | "stopped"
  | "issues";
export type CloudProductFilter = "all" | CloudInstance["product"];

export const ACTIVE_CLOUD_OPERATION_STATUSES: ReadonlySet<CloudOperationStatus> =
  new Set(["PENDING", "IN_FLIGHT", "WAITING", "UNCERTAIN"]);

const stateRank: Record<CloudInstanceState, number> = {
  starting: 0,
  stopping: 0,
  creating: 1,
  error: 2,
  unknown: 2,
  running: 3,
  stopped: 4,
};

export function isCloudOperationActive(
  status: CloudOperationStatus | undefined,
) {
  return status ? ACTIVE_CLOUD_OPERATION_STATUSES.has(status) : false;
}

export function effectiveCloudState(
  instance: CloudInstance,
  operation = instance.management?.operation ?? null,
): CloudInstanceState {
  if (operation?.status === "CONFIRMED") return operation.desired_state;
  if (operation && isCloudOperationActive(operation.status))
    return operation.desired_state === "running" ? "starting" : "stopping";
  return instance.state;
}

export function canChangeCloudPower(
  instance: CloudInstance,
  desired: "running" | "stopped",
  operation = instance.management?.operation ?? null,
) {
  if (operation && isCloudOperationActive(operation.status)) return false;
  const state = effectiveCloudState(instance, operation);
  return desired === "running" ? state === "stopped" : state === "running";
}

export function cloudOperationLabel(
  status: CloudOperationStatus,
  desired: "running" | "stopped",
) {
  const action = desired === "running" ? "开机" : "关机";
  const statusLabel: Record<CloudOperationStatus, string> = {
    PENDING: "已排队",
    IN_FLIGHT: "正在调用 AutoDL",
    WAITING: "等待状态收敛",
    UNCERTAIN: "结果待核实",
    CONFIRMED: "已确认",
    FAILED: "失败",
  };
  return `${action} · ${statusLabel[status]}`;
}

export function cloudOperationDuration(
  operation: CloudOperationView,
  now = Date.now(),
) {
  const timestamp = (value: string | null | undefined) => {
    const parsed = Date.parse(value ?? "");
    return Number.isFinite(parsed) ? parsed : null;
  };
  const created = timestamp(operation.created_at);
  const started = timestamp(operation.started_at) ?? created;
  const dispatched = timestamp(operation.dispatch_attempted_at) ?? started;
  const acknowledged = timestamp(operation.dispatch_ack_at) ?? dispatched;
  const completed =
    timestamp(operation.completed_at) ?? timestamp(operation.updated_at) ?? now;
  let label = "操作累计";
  let start = created ?? started;
  let end = completed;
  if (operation.status === "PENDING") {
    label = "排队";
    start = created;
    end = now;
  } else if (operation.status === "IN_FLIGHT") {
    label = "调用 AutoDL";
    start = dispatched;
    end = now;
  } else if (operation.status === "WAITING") {
    label = "等待状态收敛";
    start = acknowledged;
    end = now;
  } else if (operation.status === "UNCERTAIN") {
    label = "结果核验";
    start = dispatched;
    end = now;
  }
  if (start === null) return "阶段耗时等待后端上报";
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  if (seconds < 60) return `${label} ${seconds} 秒`;
  return `${label} ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function cloudThermalState(instance: CloudInstance) {
  const state = effectiveCloudState(instance);
  if (state === "running") return "热态运行";
  if (state === "stopped") return "冷态关机";
  if (state === "starting") return "冷启动中";
  if (state === "stopping") return "热态关机中";
  return "状态待确认";
}

export function cloudSchedulingSummary(instance: CloudInstance) {
  const management = instance.management;
  if (!management?.managed)
    return { tone: "muted", label: "未纳管", reason: "尚未建立调度管理关系" };
  if (!management.node_id)
    return {
      tone: "warning",
      label: "未绑定节点",
      reason: "云实例尚未关联 GPU Control 节点",
    };
  if (!management.scheduling_enabled)
    return {
      tone: "warning",
      label: "不接单",
      reason: "节点已绑定，但云调度资格尚未启用",
    };
  if (effectiveCloudState(instance) !== "running")
    return {
      tone: "warning",
      label: "暂不接单",
      reason: "实例必须稳定运行后才可参与调度",
    };
  return {
    tone: "healthy",
    label: "可参与接单",
    reason: "已绑定节点并启用云调度；最终分配仍以任务兼容性为准",
  };
}

export function filterCloudInstances(
  rows: CloudInstance[],
  filter: CloudFilter,
  product: CloudProductFilter,
  query: string,
) {
  const needle = query.trim().toLocaleLowerCase();
  return rows
    .filter((instance) => product === "all" || instance.product === product)
    .filter((instance) => {
      const state = effectiveCloudState(instance);
      if (filter === "all") return true;
      if (filter === "transitioning")
        return ["starting", "stopping", "creating"].includes(state);
      if (filter === "issues")
        return (
          ["error", "unknown"].includes(state) ||
          Boolean(instance.snapshot_error)
        );
      return state === filter;
    })
    .filter((instance) => {
      if (!needle) return true;
      return [
        instance.name,
        instance.instance_id,
        instance.gpu_spec,
        instance.region,
        instance.management?.node_id,
      ].some((value) => value?.toLocaleLowerCase().includes(needle));
    })
    .sort((left, right) => {
      const stateDifference =
        stateRank[effectiveCloudState(left)] -
        stateRank[effectiveCloudState(right)];
      if (stateDifference) return stateDifference;
      return (left.name || left.instance_id).localeCompare(
        right.name || right.instance_id,
        "zh-CN",
        { numeric: true },
      );
    });
}

export function formatCloudTimestamp(
  value: string | number | null | undefined,
) {
  if (value === null || value === undefined || value === "") return "—";
  const numeric =
    typeof value === "number"
      ? value
      : /^\d+(?:\.\d+)?$/.test(value)
        ? Number(value)
        : Number.NaN;
  const parsed = new Date(
    Number.isFinite(numeric)
      ? numeric < 10_000_000_000
        ? numeric * 1000
        : numeric
      : value,
  );
  if (!Number.isFinite(parsed.getTime())) return "—";
  return parsed.toLocaleString("zh-CN", { hour12: false });
}

export function formatCloudScheduleInput(value: string | null | undefined) {
  if (!value) return "";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}T${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

export type CloudScheduleValidation =
  | {
      valid: true;
      scheduled_start_at: string | null;
      scheduled_stop_at: string | null;
    }
  | { valid: false; error: string };

export function validateCloudSchedule(
  startInput: string,
  stopInput: string,
  now = Date.now(),
): CloudScheduleValidation {
  const earliestAllowed = now + 60_000;
  const latestAllowed = now + 365 * 24 * 60 * 60 * 1000;

  function parse(
    value: string,
    label: string,
  ):
    | { valid: true; value: string | null; timestamp: number | null }
    | { valid: false; error: string } {
    if (!value)
      return {
        valid: true,
        value: null as string | null,
        timestamp: null as number | null,
      };
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp))
      return { valid: false, error: `${label}格式无效` };
    if (timestamp < earliestAllowed)
      return { valid: false, error: `${label}必须至少晚于当前时间 1 分钟` };
    if (timestamp > latestAllowed)
      return { valid: false, error: `${label}不能超过未来 365 天` };
    return {
      valid: true,
      value: new Date(timestamp).toISOString(),
      timestamp,
    };
  }

  const start = parse(startInput, "定时开机时间");
  if (!start.valid) return { valid: false, error: start.error };
  const stop = parse(stopInput, "定时关机时间");
  if (!stop.valid) return { valid: false, error: stop.error };
  if (
    start.timestamp !== null &&
    stop.timestamp !== null &&
    start.timestamp >= stop.timestamp
  )
    return { valid: false, error: "同时配置时，定时关机必须晚于定时开机" };
  return {
    valid: true,
    scheduled_start_at: start.value,
    scheduled_stop_at: stop.value,
  };
}
