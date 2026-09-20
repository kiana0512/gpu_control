<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Clock,
  Connection,
  Refresh,
  Search,
  SwitchButton,
} from "@element-plus/icons-vue";
import { api, session } from "../api";
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
  isCloudOperationActive,
  validateCloudSchedule,
  type CloudFilter,
  type CloudProductFilter,
} from "../cloudPresentation";
import { useAutoRefresh } from "../composables/useAutoRefresh";
import { resolveCloudInstanceComfyUiAccessUrl } from "../nodePresentation";
import type {
  CloudInstance,
  CloudInstanceState,
  CloudOperationInfo,
  CloudOperationView,
  CloudServerOverview,
  CloudSshCredentials,
  CloudStateResult,
} from "../types";

type FocusTarget = { focus: () => void };

const overview = ref<CloudServerOverview | null>(null);
const partialProviderErrors = ref<Array<{ scope: string; code: string }>>([]);
const error = ref("");
const filter = ref<CloudFilter>("all");
const productFilter = ref<CloudProductFilter>("all");
const search = ref("");
const viewMode = ref<"resource" | "compact">("resource");
const expandedRefs = ref(new Set<string>());
const busyRefs = ref(new Set<string>());
const selectedRefs = ref(new Set<string>());
const batchBusy = ref(false);
const operationOverlays = ref<Record<string, CloudOperationInfo>>({});
const operationErrors = ref<Record<string, string>>({});
const credentials = ref<CloudSshCredentials | null>(null);
const credentialsFor = ref<CloudInstance | null>(null);
const revealPassword = ref(false);
const credentialsDialog = ref<FocusTarget | null>(null);
const scheduleDialog = ref<FocusTarget | null>(null);
const scheduleTarget = ref<CloudInstance | null>(null);
const scheduleForm = ref({ start: "", stop: "" });
const scheduleError = ref("");
const scheduleSaving = ref(false);
const browserTimeZone =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "浏览器本地时区";
const canOperate = computed(() =>
  ["admin", "operator"].includes(session.role() ?? ""),
);
let credentialTimer: number | undefined;
let credentialsReturnFocus: FocusTarget | null = null;
let scheduleReturnFocus: FocusTarget | null = null;
let forceRefreshRequested = false;
let latestLoadGeneration = 0;
let hasCompleteOverview = false;
let disposed = false;
const operationPollTokens = new Map<
  string,
  { token: symbol; operationId: string }
>();
const clock = ref(Date.now());
let clockTimer: number | undefined;

const presentedInstances = computed(() =>
  (overview.value?.instances ?? []).map((instance) => {
    const operation = operationOverlays.value[instance.ref];
    if (!operation) return instance;
    return {
      ...instance,
      management: {
        ...instance.management,
        managed: instance.management?.managed ?? true,
        scheduling_enabled: instance.management?.scheduling_enabled ?? false,
        desired_state: operation.desired_state,
        node_id: instance.management?.node_id ?? null,
        operation,
      },
    };
  }),
);

const instances = computed(() => {
  const rows = filterCloudInstances(
    presentedInstances.value,
    filter.value === "issues" ? "all" : filter.value,
    productFilter.value,
    search.value,
  );
  if (filter.value !== "issues") return rows;
  return rows.filter(
    (instance) =>
      ["error", "unknown"].includes(displayState(instance)) ||
      Boolean(instance.snapshot_error) ||
      Boolean(operationErrors.value[instance.ref]),
  );
});

const instanceGroups = computed(() => {
  const running: CloudInstance[] = [];
  const attention: CloudInstance[] = [];
  const idle: CloudInstance[] = [];
  for (const instance of instances.value) {
    const state = displayState(instance);
    if (["running", "starting", "stopping", "creating"].includes(state)) {
      running.push(instance);
    } else if (
      ["error", "unknown"].includes(state) ||
      instance.snapshot_error ||
      operationErrors.value[instance.ref]
    ) {
      attention.push(instance);
    } else {
      idle.push(instance);
    }
  }
  return [
    {
      key: "running",
      eyebrow: "ACTIVE RESOURCES",
      title: "运行与处理中",
      description: "优先展示正在计费、可访问或电源状态正在切换的实例。",
      instances: running,
    },
    {
      key: "attention",
      eyebrow: "NEEDS ATTENTION",
      title: "需要关注",
      description: "状态未知、指标异常或操作跟踪需要确认的实例。",
      instances: attention,
    },
    {
      key: "idle",
      eyebrow: "AVAILABLE CAPACITY",
      title: "已停止与待用",
      description: "当前未运行，可按需开机或配置下一次启停计划。",
      instances: idle,
    },
  ].filter((group) => group.instances.length > 0);
});

const selectedInstances = computed(() =>
  instances.value.filter((instance) => selectedRefs.value.has(instance.ref)),
);
const batchStartTargets = computed(() =>
  canOperate.value
    ? selectedInstances.value.filter(
        (instance) =>
          canChangeCloudPower(instance, "running") &&
          !busyRefs.value.has(instance.ref),
      )
    : [],
);
const batchStopTargets = computed(() =>
  canOperate.value
    ? selectedInstances.value.filter(
        (instance) =>
          canChangeCloudPower(instance, "stopped") &&
          !busyRefs.value.has(instance.ref),
      )
    : [],
);
const allVisibleSelected = computed(
  () =>
    instances.value.length > 0 &&
    instances.value.every((instance) => selectedRefs.value.has(instance.ref)),
);
const activeOperationCount = computed(
  () =>
    presentedInstances.value.filter((instance) =>
      isCloudOperationActive(instance.management?.operation?.status),
    ).length,
);
const displayedSummary = computed(() => {
  const states = presentedInstances.value.map((instance) =>
    displayState(instance),
  );
  return {
    total: states.length,
    running: states.filter((state) => state === "running").length,
    transitioning: states.filter((state) =>
      ["starting", "stopping", "creating"].includes(state),
    ).length,
    stopped: states.filter((state) => state === "stopped").length,
    issues: presentedInstances.value.filter(
      (instance) =>
        ["error", "unknown"].includes(displayState(instance)) ||
        Boolean(instance.snapshot_error) ||
        Boolean(operationErrors.value[instance.ref]),
    ).length,
  };
});
const providerSyncAge = computed(() => {
  const syncedAt = Date.parse(overview.value?.synced_at ?? "");
  if (!Number.isFinite(syncedAt)) return "供应商快照待同步";
  const seconds = Math.max(0, Math.floor((clock.value - syncedAt) / 1000));
  if (seconds < 10) return "供应商快照刚刚更新";
  if (seconds < 60) return `供应商快照 ${seconds} 秒前`;
  return `供应商快照 ${Math.floor(seconds / 60)} 分钟前`;
});
const minimumScheduleTime = computed(() =>
  formatCloudScheduleInput(
    new Date(
      Math.ceil((clock.value + 120_000) / 60_000) * 60_000,
    ).toISOString(),
  ),
);
const scheduleConfigured = computed(
  () =>
    Boolean(scheduleTarget.value?.scheduled_start_at) ||
    Boolean(scheduleTarget.value?.scheduled_stop_at),
);

watch(
  instances,
  (visibleInstances) => {
    const visibleRefs = new Set(
      visibleInstances.map((instance) => instance.ref),
    );
    const next = new Set(
      [...selectedRefs.value].filter((refValue) => visibleRefs.has(refValue)),
    );
    if (
      next.size !== selectedRefs.value.size ||
      [...next].some((refValue) => !selectedRefs.value.has(refValue))
    )
      selectedRefs.value = next;
  },
  { flush: "sync" },
);

const stateCopy: Record<CloudInstanceState, string> = {
  running: "运行中",
  stopped: "已关机",
  starting: "启动中",
  stopping: "关机中",
  creating: "创建中",
  error: "异常",
  unknown: "状态未知",
};

function instanceTitle(instance: CloudInstance) {
  return instance.name || instance.gpu_spec || instance.instance_id;
}

function gpuDescription(instance: CloudInstance) {
  const spec = instance.gpu_spec || "GPU 规格待同步";
  return instance.gpu_count > 1 ? `${spec} × ${instance.gpu_count}` : spec;
}

function billingDescription(instance: CloudInstance) {
  if (instance.price_yuan_per_hour !== null)
    return `¥${instance.price_yuan_per_hour.toFixed(2)} / 小时`;
  if (instance.charge_type) return instance.charge_type;
  return "账单信息待同步";
}

function diskDescription(instance: CloudInstance) {
  if (instance.disk_total_gib === null) return "—";
  const used = instance.disk_used_gib;
  return used === null
    ? `${instance.disk_total_gib.toFixed(0)} GB`
    : `${used.toFixed(1)} / ${instance.disk_total_gib.toFixed(1)} GB`;
}

function operationFor(instance: CloudInstance): CloudOperationView | null {
  return (
    operationOverlays.value[instance.ref] ??
    instance.management?.operation ??
    null
  );
}

function displayState(instance: CloudInstance) {
  return effectiveCloudState(instance, operationFor(instance));
}

function operationStep(instance: CloudInstance) {
  const operation = operationFor(instance);
  return operation
    ? cloudOperationLabel(operation.status, operation.desired_state)
    : stateCopy[displayState(instance)];
}

function operationDuration(instance: CloudInstance) {
  const operation = operationFor(instance);
  if (!operation) return "无进行中的电源操作";
  return cloudOperationDuration(operation, clock.value);
}

function detailsVisible(instance: CloudInstance) {
  return viewMode.value === "resource" || expandedRefs.value.has(instance.ref);
}

function toggleDetails(instance: CloudInstance) {
  const next = new Set(expandedRefs.value);
  if (next.has(instance.ref)) next.delete(instance.ref);
  else next.add(instance.ref);
  expandedRefs.value = next;
}

function actionability(instance: CloudInstance) {
  if (!canOperate.value) return { tone: "readonly", label: "只读" };
  if (busyRefs.value.has(instance.ref))
    return { tone: "pending", label: "正在提交" };
  if (isCloudOperationActive(operationFor(instance)?.status))
    return { tone: "pending", label: "状态收敛中" };
  if (canChangeCloudPower(instance, "stopped"))
    return { tone: "ready", label: "可关机" };
  if (canChangeCloudPower(instance, "running"))
    return { tone: "ready", label: "可开机" };
  return { tone: "blocked", label: "暂不可操作" };
}

function partialErrorScope(scope: string) {
  if (scope === "balance") return "余额";
  if (scope === "app") return "应用实例";
  if (scope === "pro") return "专业实例";
  return scope;
}

function scheduling(instance: CloudInstance) {
  if (isCloudOperationActive(operationFor(instance)?.status))
    return {
      tone: "warning",
      label: "切换中",
      reason: "电源状态正在收敛，暂不参与新任务分配",
    };
  return cloudSchedulingSummary(instance);
}

function schedulingScope(instance: CloudInstance) {
  if (instance.management?.node_id === "autodl-5090-01")
    return {
      label: "仅局部重绘",
      detail: "只接收 modelview-inpaint；粗糙度及其他任务走本地节点",
    };
  return {
    label: instance.management?.scheduling_enabled ? "按兼容策略" : "未启用",
    detail: "以节点工作流白名单与实时兼容性为准",
  };
}

function accessUrl(
  instance: CloudInstance,
  kind: "jupyter" | "service_6006" | "service_6008",
) {
  if (kind === "service_6006")
    return resolveCloudInstanceComfyUiAccessUrl(
      instance,
      window.location.hostname,
    );
  return instance.access[kind];
}

function openAccess(
  instance: CloudInstance,
  kind: "jupyter" | "service_6006" | "service_6008",
) {
  const url = accessUrl(instance, kind);
  if (!url) {
    ElMessage.warning("该访问入口当前不可用");
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

function setBusy(refValue: string, busy: boolean) {
  const next = new Set(busyRefs.value);
  if (busy) next.add(refValue);
  else next.delete(refValue);
  busyRefs.value = next;
}

async function load(force = false) {
  const generation = ++latestLoadGeneration;
  try {
    const result = await api.cloudServers(force);
    if (disposed || generation !== latestLoadGeneration) return;
    const partialErrors = result.partial_errors ?? [];
    partialProviderErrors.value = partialErrors;
    if (partialErrors.length && hasCompleteOverview) {
      error.value = "";
      resumeOperationPolling(overview.value?.instances ?? []);
      return;
    }
    overview.value = result;
    if (!partialErrors.length) hasCompleteOverview = true;
    error.value = "";
    const knownRefs = new Set(result.instances.map((instance) => instance.ref));
    selectedRefs.value = new Set(
      [...selectedRefs.value].filter((refValue) => knownRefs.has(refValue)),
    );
    const nextOverlays = { ...operationOverlays.value };
    const nextOperationErrors = { ...operationErrors.value };
    for (const instance of result.instances) {
      const overlay = nextOverlays[instance.ref];
      const reported = instance.management?.operation;
      if (
        overlay &&
        ((reported && reported.id !== overlay.id) ||
          (!reported && instance.state === overlay.desired_state))
      )
        delete nextOverlays[instance.ref];
      if (
        !reported &&
        instance.management?.desired_state === instance.state &&
        !["error", "unknown"].includes(instance.state)
      )
        delete nextOperationErrors[instance.ref];
    }
    operationOverlays.value = nextOverlays;
    operationErrors.value = nextOperationErrors;
    resumeOperationPolling(result.instances);
  } catch (cause) {
    if (disposed || generation !== latestLoadGeneration) return;
    error.value =
      cause instanceof Error ? cause.message : "云服务器数据加载失败";
    throw cause;
  }
}

async function loadRequestedInventory() {
  let lastFailure: unknown;
  do {
    const force = forceRefreshRequested;
    forceRefreshRequested = false;
    try {
      await load(force);
      lastFailure = undefined;
    } catch (cause) {
      lastFailure = cause;
    }
  } while (!disposed && forceRefreshRequested);
  if (lastFailure) throw lastFailure;
}

const { run, refreshing, lastUpdatedAt } = useAutoRefresh(
  loadRequestedInventory,
  15_000,
);

async function refresh(force = false) {
  if (force) forceRefreshRequested = true;
  await run();
}

function setOperationOverlay(refValue: string, operation: CloudOperationInfo) {
  operationOverlays.value = {
    ...operationOverlays.value,
    [refValue]: operation,
  };
}

function setOperationError(refValue: string, message = "") {
  const next = { ...operationErrors.value };
  if (message) next[refValue] = message;
  else delete next[refValue];
  operationErrors.value = next;
}

function operationFromResult(result: CloudStateResult): CloudOperationInfo {
  return {
    id: result.operation_id,
    operation_id: result.operation_id,
    status: result.status,
    desired_state: result.desired_state,
  };
}

function wait(milliseconds: number) {
  return new Promise<void>((resolve) =>
    window.setTimeout(resolve, milliseconds),
  );
}

async function pollOperation(
  instance: CloudInstance,
  result: Pick<CloudStateResult, "operation_id" | "status" | "desired_state">,
  announceTerminal: boolean,
) {
  const token = Symbol(result.operation_id);
  operationPollTokens.set(instance.ref, {
    token,
    operationId: result.operation_id,
  });
  let readFailures = 0;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await wait(attempt < 15 ? 1_000 : 2_000);
    if (disposed || operationPollTokens.get(instance.ref)?.token !== token)
      return;
    try {
      const current = await api.cloudOperation(result.operation_id);
      if (disposed || operationPollTokens.get(instance.ref)?.token !== token)
        return;
      readFailures = 0;
      setOperationError(instance.ref);
      setOperationOverlay(instance.ref, current);
      if (current.status === "CONFIRMED") {
        if (announceTerminal)
          ElMessage.success(
            current.desired_state === "running"
              ? "实例已确认开机"
              : "实例已确认关机",
          );
        operationPollTokens.delete(instance.ref);
        await refresh(true);
        return;
      }
      if (current.status === "FAILED") {
        const message =
          current.error_message || current.error_code || "云电源操作失败";
        setOperationError(instance.ref, message);
        if (announceTerminal) ElMessage.error(message);
        operationPollTokens.delete(instance.ref);
        await refresh(true);
        return;
      }
    } catch (cause) {
      if (disposed || operationPollTokens.get(instance.ref)?.token !== token)
        return;
      readFailures += 1;
      if (readFailures >= 3)
        setOperationError(
          instance.ref,
          cause instanceof Error
            ? `操作状态暂时无法读取：${cause.message}`
            : "操作状态暂时无法读取，将继续从实例清单恢复",
        );
    }
  }
  operationPollTokens.delete(instance.ref);
  setOperationError(instance.ref, "操作仍在后台收敛，页面将继续自动刷新");
  await refresh(true);
}

function resumeOperationPolling(rows: CloudInstance[]) {
  if (!canOperate.value) return;
  for (const instance of rows) {
    const operation = instance.management?.operation;
    if (!operation || !isCloudOperationActive(operation.status)) continue;
    const tracked = operationPollTokens.get(instance.ref);
    if (tracked?.operationId === operation.id) continue;
    void pollOperation(
      instance,
      {
        operation_id: operation.id,
        status: operation.status,
        desired_state: operation.desired_state,
      },
      false,
    );
  }
}

async function submitPower(
  instance: CloudInstance,
  desired: "running" | "stopped",
  announceTerminal = true,
) {
  if (!canOperate.value)
    throw new Error("当前账号为只读权限，不能执行电源操作");
  setBusy(instance.ref, true);
  setOperationError(instance.ref);
  try {
    const result =
      desired === "running"
        ? await api.startCloudInstance(instance.product, instance.instance_id)
        : await api.stopCloudInstance(instance.product, instance.instance_id);
    setOperationOverlay(instance.ref, operationFromResult(result));
    void pollOperation(instance, result, announceTerminal);
    return result;
  } finally {
    setBusy(instance.ref, false);
  }
}

async function changePower(
  instance: CloudInstance,
  desired: "running" | "stopped",
) {
  const verb = desired === "running" ? "启动" : "关闭";
  const consequence =
    desired === "running"
      ? "如果实例已经运行，后端会按幂等操作处理，不会重复开机。"
      : "关机会立即中断该实例上的服务与任务，请先确认没有生产任务运行。";
  try {
    await ElMessageBox.confirm(
      `确认${verb} ${instanceTitle(instance)} 吗？${consequence}`,
      `${verb} AutoDL 实例`,
      {
        type: desired === "running" ? "info" : "warning",
        confirmButtonText: `确认${verb}`,
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }
  try {
    const result = await submitPower(instance, desired);
    ElMessage.success(
      result.accepted ? `${verb}请求已提交` : `实例已处于目标状态`,
    );
    void refresh(false);
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : `${verb}失败`);
  }
}

function toggleSelected(refValue: string, selected: boolean) {
  const next = new Set(selectedRefs.value);
  if (selected) next.add(refValue);
  else next.delete(refValue);
  selectedRefs.value = next;
}

function onSelectionChange(refValue: string, event: Event) {
  toggleSelected(refValue, (event.target as HTMLInputElement).checked);
}

function toggleAllVisible() {
  const next = new Set(selectedRefs.value);
  if (allVisibleSelected.value)
    for (const instance of instances.value) next.delete(instance.ref);
  else for (const instance of instances.value) next.add(instance.ref);
  selectedRefs.value = next;
}

async function batchPower(desired: "running" | "stopped") {
  let targets =
    desired === "running" ? batchStartTargets.value : batchStopTargets.value;
  if (!targets.length) return;
  const verb = desired === "running" ? "开机" : "关机";
  const targetNames = targets.map(instanceTitle).join("、");
  try {
    await ElMessageBox.confirm(
      desired === "running"
        ? `将向 ${targets.length} 台已关机实例提交开机请求，实例启动后会开始计费。\n\n目标：${targetNames}`
        : `将向 ${targets.length} 台运行实例提交关机请求。后端会拒绝仍有任务、租约或外部队列的实例。\n\n目标：${targetNames}`,
      `批量${verb}`,
      {
        type: desired === "running" ? "info" : "warning",
        confirmButtonText: `确认批量${verb}`,
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }
  // Filters can change while the confirmation is open. Re-read the current
  // visible selection so a batch action can never leak to a hidden resource.
  targets =
    desired === "running" ? batchStartTargets.value : batchStopTargets.value;
  if (!targets.length) {
    ElMessage.warning("当前筛选结果中已没有可执行的实例");
    return;
  }
  batchBusy.value = true;
  const results = await Promise.allSettled(
    targets.map(async (instance) => {
      try {
        return await submitPower(instance, desired, false);
      } catch (cause) {
        setOperationError(
          instance.ref,
          cause instanceof Error ? cause.message : `${verb}请求提交失败`,
        );
        throw cause;
      }
    }),
  );
  const succeeded = results.filter(
    (result) => result.status === "fulfilled",
  ).length;
  const failed = results.length - succeeded;
  if (succeeded) ElMessage.success(`${succeeded} 台实例的${verb}请求已提交`);
  if (failed) ElMessage.error(`${failed} 台实例提交失败，请查看实例卡片并重试`);
  selectedRefs.value = new Set();
  batchBusy.value = false;
  void refresh(false);
}

function focusTarget(value: unknown): FocusTarget | null {
  return value && typeof (value as FocusTarget).focus === "function"
    ? (value as FocusTarget)
    : null;
}

function restoreFocus(target: FocusTarget | null) {
  if (!target || disposed) return;
  window.requestAnimationFrame(() => target.focus());
}

function closeCredentials(restore = true) {
  credentials.value = null;
  credentialsFor.value = null;
  revealPassword.value = false;
  if (credentialTimer) window.clearTimeout(credentialTimer);
  credentialTimer = undefined;
  const returnFocus = credentialsReturnFocus;
  credentialsReturnFocus = null;
  if (restore) restoreFocus(returnFocus);
}

function openSchedule(instance: CloudInstance, event?: globalThis.MouseEvent) {
  if (!canOperate.value) {
    ElMessage.warning("当前账号为只读权限，不能修改定时启停计划");
    return;
  }
  closeCredentials(false);
  scheduleReturnFocus =
    focusTarget(event?.currentTarget) ?? focusTarget(document.activeElement);
  scheduleTarget.value = instance;
  scheduleForm.value = {
    start: formatCloudScheduleInput(instance.scheduled_start_at),
    stop: formatCloudScheduleInput(instance.scheduled_stop_at),
  };
  scheduleError.value = "";
  void nextTick(() => scheduleDialog.value?.focus());
}

function closeSchedule(force = false, restore = true) {
  if (scheduleSaving.value && !force) return;
  scheduleTarget.value = null;
  scheduleForm.value = { start: "", stop: "" };
  scheduleError.value = "";
  const returnFocus = scheduleReturnFocus;
  scheduleReturnFocus = null;
  if (restore) restoreFocus(returnFocus);
}

function updateLocalSchedule(
  refValue: string,
  scheduledStartAt: string | null,
  scheduledStopAt: string | null,
) {
  const instance = overview.value?.instances.find(
    (row) => row.ref === refValue,
  );
  if (!instance) return;
  instance.scheduled_start_at = scheduledStartAt;
  instance.scheduled_stop_at = scheduledStopAt;
}

async function saveSchedule(clear = false) {
  const target = scheduleTarget.value;
  if (!target) return;
  const validation = clear
    ? {
        valid: true as const,
        scheduled_start_at: null,
        scheduled_stop_at: null,
      }
    : validateCloudSchedule(scheduleForm.value.start, scheduleForm.value.stop);
  if (!validation.valid) {
    scheduleError.value = validation.error;
    return;
  }
  const clearing =
    validation.scheduled_start_at === null &&
    validation.scheduled_stop_at === null;
  const details = [
    validation.scheduled_start_at
      ? `开机：${formatCloudTimestamp(validation.scheduled_start_at)}`
      : "不开启定时开机",
    validation.scheduled_stop_at
      ? `关机：${formatCloudTimestamp(validation.scheduled_stop_at)}`
      : "不开启定时关机",
  ].join("；");
  try {
    await ElMessageBox.confirm(
      clearing
        ? `确认清除 ${instanceTitle(target)} 的全部定时启停计划吗？清除后系统不会在原计划时间自动操作。`
        : `确认保存 ${instanceTitle(target)} 的一次性启停计划吗？${details}。时间按 ${browserTimeZone} 录入，到点后仍由后端安全门禁执行。`,
      clearing ? "清除定时启停" : "保存定时启停",
      {
        type: clearing ? "warning" : "info",
        confirmButtonText: clearing ? "确认清除" : "确认保存",
        cancelButtonText: "取消",
      },
    );
  } catch {
    return;
  }

  scheduleSaving.value = true;
  scheduleError.value = "";
  setBusy(target.ref, true);
  let saved = false;
  try {
    const result = await api.updateCloudSchedule(
      target.product,
      target.instance_id,
      validation.scheduled_start_at,
      validation.scheduled_stop_at,
    );
    updateLocalSchedule(
      target.ref,
      result.scheduled_start_at,
      result.scheduled_stop_at,
    );
    saved = true;
    ElMessage.success(clearing ? "定时启停计划已清除" : "定时启停计划已保存");
  } catch (cause) {
    scheduleError.value =
      cause instanceof Error ? cause.message : "定时启停计划保存失败";
    ElMessage.error(scheduleError.value);
  } finally {
    scheduleSaving.value = false;
    setBusy(target.ref, false);
  }
  if (saved) {
    closeSchedule(true);
    void refresh(false);
  }
}

async function showSsh(instance: CloudInstance, event?: globalThis.MouseEvent) {
  if (!canOperate.value) {
    ElMessage.warning("当前账号为只读权限，不能读取 SSH 凭据");
    return;
  }
  setBusy(instance.ref, true);
  closeCredentials(false);
  credentialsReturnFocus =
    focusTarget(event?.currentTarget) ?? focusTarget(document.activeElement);
  try {
    credentials.value = await api.cloudSshCredentials(
      instance.product,
      instance.instance_id,
    );
    credentialsFor.value = instance;
    credentialTimer = window.setTimeout(closeCredentials, 60_000);
    await nextTick();
    credentialsDialog.value?.focus();
  } catch (cause) {
    ElMessage.error(
      cause instanceof Error ? cause.message : "SSH 凭据获取失败",
    );
    restoreFocus(credentialsReturnFocus);
    credentialsReturnFocus = null;
  } finally {
    setBusy(instance.ref, false);
  }
}

async function copy(value: string, label: string) {
  try {
    await window.navigator.clipboard.writeText(value);
    ElMessage.success(`${label}已复制`);
  } catch {
    ElMessage.error("浏览器拒绝访问剪贴板，请手动复制");
  }
}

const sshCommand = computed(() => {
  const value = credentials.value;
  return value ? `ssh -p ${value.port} ${value.username}@${value.host}` : "";
});

onMounted(() => {
  clockTimer = window.setInterval(() => {
    clock.value = Date.now();
  }, 1_000);
});

onBeforeUnmount(() => {
  disposed = true;
  latestLoadGeneration += 1;
  operationPollTokens.clear();
  closeCredentials();
  closeSchedule(true);
  if (clockTimer) window.clearInterval(clockTimer);
});
</script>

<template>
  <div class="page cloud-page">
    <div class="page-heading">
      <div>
        <div class="eyebrow">AutoDL 云算力</div>
        <h1>云服务器</h1>
        <p>统一管理云端 GPU 实例、开关机状态与 SSH 入口</p>
      </div>
      <div class="heading-actions">
        <span class="refresh-state"
          ><i :class="{ spinning: refreshing }"></i
          >{{ activeOperationCount ? "操作快速跟踪" : "自动刷新 · 15 秒"
          }}<br /><small
            >最后更新
            {{
              lastUpdatedAt?.toLocaleTimeString("zh-CN", { hour12: false }) ??
              "等待首次同步"
            }}</small
          ></span
        >
        <button class="secondary" @click="refresh(true)">
          <el-icon><Refresh /></el-icon>立即同步
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner persistent-error">
      <strong>AutoDL 同步失败</strong><span>{{ error }}</span
      ><button @click="refresh(true)">重试</button>
    </div>

    <div
      v-if="partialProviderErrors.length"
      class="error-banner persistent-error partial-provider-warning"
    >
      <strong>AutoDL 部分数据未同步</strong>
      <span>
        {{
          partialProviderErrors
            .map((item) => `${partialErrorScope(item.scope)}：${item.code}`)
            .join("；")
        }}
      </span>
      <button @click="refresh(true)">立即重试</button>
    </div>

    <section class="cloud-summary">
      <button :class="{ active: filter === 'all' }" @click="filter = 'all'">
        <span>总实例</span
        ><strong>{{ overview ? displayedSummary.total : "—" }}</strong>
      </button>
      <button
        class="running"
        :class="{ active: filter === 'running' }"
        @click="filter = 'running'"
      >
        <span>运行中</span
        ><strong>{{ overview ? displayedSummary.running : "—" }}</strong>
      </button>
      <button
        class="transitioning"
        :class="{ active: filter === 'transitioning' }"
        @click="filter = 'transitioning'"
      >
        <span>状态切换中</span
        ><strong>{{ overview ? displayedSummary.transitioning : "—" }}</strong>
      </button>
      <button
        :class="{ active: filter === 'stopped' }"
        @click="filter = 'stopped'"
      >
        <span>已关机</span
        ><strong>{{ overview ? displayedSummary.stopped : "—" }}</strong>
      </button>
      <button
        class="issues"
        :class="{ active: filter === 'issues' }"
        @click="filter = 'issues'"
      >
        <span>需要关注</span
        ><strong>{{ overview ? displayedSummary.issues : "—" }}</strong>
      </button>
      <div class="balance">
        <span>可用余额</span>
        <strong>{{
          overview ? `¥${overview.balance.available_yuan.toFixed(2)}` : "—"
        }}</strong>
        <small v-if="overview"
          >代金券 ¥{{ overview.balance.voucher_yuan.toFixed(2) }}</small
        >
      </div>
    </section>

    <section class="cloud-context" aria-label="云资源操作说明">
      <div>
        <span class="context-kicker">独立资源池</span>
        <strong>云节点单独验收后参与调度</strong>
      </div>
      <p>
        电源切换期间自动锁定危险操作；SSH 凭据只按需读取，并在 60 秒后清除。
      </p>
      <span class="permission-chip" :class="{ readonly: !canOperate }">
        {{ canOperate ? "可执行资源操作" : "当前账号只读" }}
      </span>
    </section>

    <section class="cloud-toolbar">
      <label class="cloud-search">
        <el-icon><Search /></el-icon>
        <input
          v-model.trim="search"
          type="search"
          placeholder="搜索名称、实例 ID、GPU、区域或绑定节点"
        />
      </label>
      <select v-model="productFilter" aria-label="实例产品类型">
        <option value="all">全部产品</option>
        <option value="app">应用实例</option>
        <option value="pro">专业实例</option>
      </select>
      <div class="view-switcher" aria-label="资源展示方式">
        <button
          :class="{ active: viewMode === 'resource' }"
          :aria-pressed="viewMode === 'resource'"
          @click="viewMode = 'resource'"
        >
          资源视图
        </button>
        <button
          :class="{ active: viewMode === 'compact' }"
          :aria-pressed="viewMode === 'compact'"
          @click="viewMode = 'compact'"
        >
          紧凑视图
        </button>
      </div>
      <span class="visible-count"
        >显示 {{ instances.length }} / {{ overview?.summary.total ?? 0 }} ·
        {{ providerSyncAge }}</span
      >
      <button
        class="select-visible"
        :disabled="!instances.length"
        @click="toggleAllVisible"
      >
        {{ allVisibleSelected ? "取消本页选择" : "选择当前结果" }}
      </button>
    </section>

    <section v-if="selectedRefs.size" class="batch-bar">
      <div>
        <strong>已选择 {{ selectedRefs.size }} 台</strong>
        <span>只会操作状态稳定且符合目标状态的实例</span>
      </div>
      <div>
        <button
          class="secondary"
          :disabled="batchBusy || !batchStartTargets.length"
          @click="batchPower('running')"
        >
          批量开机 · {{ batchStartTargets.length }}
        </button>
        <button
          class="danger-outline"
          :disabled="batchBusy || !batchStopTargets.length"
          @click="batchPower('stopped')"
        >
          批量关机 · {{ batchStopTargets.length }}
        </button>
        <button
          class="text-action"
          :disabled="batchBusy"
          @click="selectedRefs = new Set()"
        >
          清除选择
        </button>
      </div>
    </section>

    <div class="resource-workspace" :class="`view-${viewMode}`">
      <section
        v-for="group in instanceGroups"
        :key="group.key"
        class="resource-group"
        :class="`group-${group.key}`"
      >
        <header class="resource-group-heading">
          <div>
            <span>{{ group.eyebrow }}</span>
            <h2>{{ group.title }}</h2>
            <p>{{ group.description }}</p>
          </div>
          <strong>{{ group.instances.length }}</strong>
        </header>

        <div class="instance-list">
          <article
            v-for="instance in group.instances"
            :key="instance.ref"
            class="instance-card"
            :class="[
              `state-${displayState(instance)}`,
              {
                selected: selectedRefs.has(instance.ref),
                compact: viewMode === 'compact',
                expanded: detailsVisible(instance),
              },
            ]"
          >
            <div class="instance-head">
              <label class="instance-selector">
                <input
                  type="checkbox"
                  :checked="selectedRefs.has(instance.ref)"
                  :disabled="!canOperate"
                  :aria-label="`选择 ${instanceTitle(instance)}`"
                  @change="onSelectionChange(instance.ref, $event)"
                />
              </label>
              <div class="instance-identity">
                <i class="state-dot"></i>
                <div>
                  <p>
                    {{ instance.product === "app" ? "应用实例" : "专业实例" }}
                    · {{ instance.region || "区域待同步" }}
                  </p>
                  <h3>{{ instanceTitle(instance) }}</h3>
                  <code>{{ instance.instance_id }}</code>
                </div>
              </div>
              <div class="instance-quickfacts">
                <div>
                  <span>GPU</span>
                  <strong>{{ gpuDescription(instance) }}</strong>
                </div>
                <div>
                  <span>调度节点</span>
                  <strong>{{
                    instance.management?.node_id || "未绑定"
                  }}</strong>
                </div>
              </div>
              <div class="state-column">
                <span class="state-pill">{{
                  stateCopy[displayState(instance)]
                }}</span>
                <span
                  class="actionability"
                  :class="`tone-${actionability(instance).tone}`"
                >
                  {{ actionability(instance).label }}
                </span>
                <small class="provider-state"
                  >AutoDL · {{ instance.provider_status || "unknown" }}</small
                >
              </div>
            </div>

            <div v-if="detailsVisible(instance)" class="instance-details">
              <div class="detail-heading">
                <span>资源与运行指标</span>
                <small>供应商快照与控制平面状态分层展示</small>
              </div>
              <div class="instance-body">
                <section class="primary-spec">
                  <span>GPU 规格</span>
                  <strong>{{ gpuDescription(instance) }}</strong>
                  <small>{{ instance.region || "区域待同步" }}</small>
                </section>
                <dl>
                  <div>
                    <dt>系统盘</dt>
                    <dd>{{ diskDescription(instance) }}</dd>
                  </div>
                  <div>
                    <dt>CPU</dt>
                    <dd>
                      {{
                        instance.cpu_percent === null
                          ? "—"
                          : `${instance.cpu_percent}%`
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>内存</dt>
                    <dd>
                      {{
                        instance.memory_percent === null
                          ? "—"
                          : `${instance.memory_percent}%`
                      }}
                    </dd>
                  </div>
                  <div>
                    <dt>计费</dt>
                    <dd>{{ billingDescription(instance) }}</dd>
                  </div>
                </dl>
                <div class="application">
                  <span>应用镜像</span>
                  <strong>{{
                    instance.application.name || "镜像信息待同步"
                  }}</strong>
                  <small v-if="instance.application.version">{{
                    instance.application.version
                  }}</small>
                </div>
              </div>

              <div class="detail-heading control-heading">
                <span>调度与生命周期</span>
                <small>用于判断实例是否可接单以及当前电源步骤</small>
              </div>
              <div class="control-evidence">
                <section
                  class="scheduling-state"
                  :class="scheduling(instance).tone"
                >
                  <span>接单与调度</span>
                  <strong>{{ scheduling(instance).label }}</strong>
                  <small>{{ scheduling(instance).reason }}</small>
                </section>
                <section>
                  <span>任务范围</span>
                  <strong>{{ schedulingScope(instance).label }}</strong>
                  <small>{{ schedulingScope(instance).detail }}</small>
                </section>
                <section>
                  <span>候选接单节点</span>
                  <strong>{{
                    instance.management?.node_id || "尚未绑定"
                  }}</strong>
                  <small>{{
                    instance.management?.scheduling_enabled
                      ? "已进入候选池，不代表当前有任务"
                      : "云调度未启用"
                  }}</small>
                </section>
                <section>
                  <span>冷启动模板</span>
                  <strong>{{
                    instance.management?.bootstrap_profile || "未配置"
                  }}</strong>
                  <small>仅展示白名单模板标识</small>
                </section>
                <section class="current-step">
                  <span>当前步骤</span>
                  <strong>{{ operationStep(instance) }}</strong>
                  <small>{{ operationDuration(instance) }}</small>
                </section>
                <section>
                  <span>冷热状态</span>
                  <strong>{{ cloudThermalState(instance) }}</strong>
                  <small
                    >最近启动
                    {{ formatCloudTimestamp(instance.started_at) }}</small
                  >
                </section>
                <section class="schedule-state">
                  <span>定时启停</span>
                  <strong
                    v-if="
                      instance.scheduled_start_at || instance.scheduled_stop_at
                    "
                  >
                    {{
                      instance.scheduled_start_at && instance.scheduled_stop_at
                        ? "已配置启停"
                        : "已配置单项计划"
                    }}
                  </strong>
                  <strong v-else>未配置</strong>
                  <small v-if="instance.scheduled_start_at">
                    开 · {{ formatCloudTimestamp(instance.scheduled_start_at) }}
                  </small>
                  <small v-if="instance.scheduled_stop_at">
                    关 · {{ formatCloudTimestamp(instance.scheduled_stop_at) }}
                  </small>
                  <small v-if="instance.timed_shutdown_at">
                    AutoDL 原生关 ·
                    {{ formatCloudTimestamp(instance.timed_shutdown_at) }}
                  </small>
                  <small
                    v-else-if="
                      !instance.scheduled_start_at &&
                      !instance.scheduled_stop_at
                    "
                  >
                    AutoDL 原生计划未上报
                  </small>
                </section>
              </div>
            </div>

            <div v-if="instance.snapshot_error" class="snapshot-warning">
              部分运行指标暂不可用 · {{ instance.snapshot_error }}
            </div>
            <div v-if="operationErrors[instance.ref]" class="operation-error">
              <strong>操作跟踪提示</strong
              ><span>{{ operationErrors[instance.ref] }}</span>
              <button @click="refresh(true)">重新同步</button>
            </div>

            <footer>
              <div class="access-actions">
                <button
                  v-if="viewMode === 'compact'"
                  class="detail-toggle"
                  :aria-expanded="detailsVisible(instance)"
                  @click="toggleDetails(instance)"
                >
                  {{ detailsVisible(instance) ? "收起详情" : "查看详情" }}
                </button>
                <button
                  v-if="instance.access.jupyter"
                  class="text-action"
                  @click="openAccess(instance, 'jupyter')"
                >
                  JupyterLab
                </button>
                <button
                  v-if="instance.access.service_6006"
                  class="text-action"
                  @click="openAccess(instance, 'service_6006')"
                >
                  {{
                    instance.management?.node_id === "autodl-5090-01"
                      ? "ComfyUI 直连"
                      : "WebUI-6006"
                  }}
                </button>
                <button
                  v-if="instance.access.service_6008"
                  class="text-action"
                  @click="openAccess(instance, 'service_6008')"
                >
                  WebUI-6008
                </button>
              </div>
              <div class="power-actions">
                <button
                  class="secondary"
                  :disabled="!canOperate || busyRefs.has(instance.ref)"
                  @click="openSchedule(instance, $event)"
                >
                  <el-icon><Clock /></el-icon>定时启停
                </button>
                <button
                  v-if="displayState(instance) === 'running'"
                  class="secondary"
                  :disabled="!canOperate || busyRefs.has(instance.ref)"
                  @click="showSsh(instance, $event)"
                >
                  <el-icon><Connection /></el-icon>SSH 连接
                </button>
                <button
                  v-if="canChangeCloudPower(instance, 'stopped')"
                  class="danger-outline"
                  :disabled="!canOperate || busyRefs.has(instance.ref)"
                  @click="changePower(instance, 'stopped')"
                >
                  <el-icon><SwitchButton /></el-icon>关机
                </button>
                <button
                  v-else-if="canChangeCloudPower(instance, 'running')"
                  class="primary"
                  :disabled="!canOperate || busyRefs.has(instance.ref)"
                  @click="changePower(instance, 'running')"
                >
                  <el-icon><SwitchButton /></el-icon>开机
                </button>
                <span v-else class="state-note">状态稳定后可操作</span>
              </div>
            </footer>
          </article>
        </div>
      </section>

      <div
        v-if="!instances.length && !refreshing"
        class="empty-state action-empty"
      >
        <strong>{{
          overview?.summary.total ? "该状态下没有实例" : "尚无云服务器实例"
        }}</strong>
        <span>调整筛选条件，或等待实例创建后自动同步。</span>
      </div>
    </div>

    <div
      v-if="credentials"
      class="panel-backdrop"
      @click.self="closeCredentials()"
    >
      <section
        ref="credentialsDialog"
        class="ssh-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ssh-dialog-title"
        tabindex="-1"
        @keydown.esc.stop.prevent="closeCredentials()"
      >
        <header>
          <div>
            <span class="modal-eyebrow">临时访问凭据</span>
            <h2 id="ssh-dialog-title">SSH 连接</h2>
            <p>
              {{
                credentialsFor
                  ? instanceTitle(credentialsFor)
                  : credentials.instance_id
              }}
            </p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            @click="closeCredentials()"
          >
            ×
          </button>
        </header>
        <div class="credential-warning">
          凭据不会保存到浏览器或审计日志，并将在 60 秒后从此页面清除。
        </div>
        <dl class="credentials-grid">
          <div>
            <dt>主机</dt>
            <dd>{{ credentials.host }}</dd>
          </div>
          <div>
            <dt>端口</dt>
            <dd>{{ credentials.port }}</dd>
          </div>
          <div>
            <dt>用户</dt>
            <dd>{{ credentials.username }}</dd>
          </div>
          <div class="password-row">
            <dt>密码</dt>
            <dd>
              {{ revealPassword ? credentials.password : "••••••••••••••••" }}
            </dd>
            <button
              class="text-action"
              @click="revealPassword = !revealPassword"
            >
              {{ revealPassword ? "隐藏" : "显示" }}
            </button>
            <button
              class="text-action"
              @click="copy(credentials.password, '密码')"
            >
              复制
            </button>
          </div>
        </dl>
        <div class="command-box">
          <code>{{ sshCommand }}</code
          ><button @click="copy(sshCommand, 'SSH 命令')">复制命令</button>
        </div>
        <footer>
          <button class="secondary" @click="closeCredentials()">关闭</button>
        </footer>
      </section>
    </div>

    <div
      v-if="scheduleTarget"
      class="panel-backdrop"
      @click.self="closeSchedule()"
    >
      <section
        ref="scheduleDialog"
        class="ssh-modal schedule-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="schedule-dialog-title"
        tabindex="-1"
        @keydown.esc.stop.prevent="closeSchedule()"
      >
        <header>
          <div>
            <span class="modal-eyebrow">一次性电源计划</span>
            <h2 id="schedule-dialog-title">定时启停</h2>
            <p>{{ instanceTitle(scheduleTarget) }}</p>
          </div>
          <button
            class="icon-button"
            aria-label="关闭"
            :disabled="scheduleSaving"
            @click="closeSchedule()"
          >
            ×
          </button>
        </header>

        <div class="schedule-timezone">
          <strong>录入时区：{{ browserTimeZone }}</strong>
          <span>保存时转换成带时区的 ISO 时间；两个计划均为一次性执行。</span>
        </div>

        <div class="schedule-fields">
          <label>
            <span>定时开机</span>
            <input
              v-model="scheduleForm.start"
              type="datetime-local"
              :min="minimumScheduleTime"
              :disabled="scheduleSaving"
            />
            <small>留空表示不安排自动开机</small>
          </label>
          <label>
            <span>定时关机</span>
            <input
              v-model="scheduleForm.stop"
              type="datetime-local"
              :min="minimumScheduleTime"
              :disabled="scheduleSaving"
            />
            <small>后端到点后仍会执行任务与租约安全检查</small>
          </label>
        </div>

        <div v-if="scheduleError" class="schedule-error">
          {{ scheduleError }}
        </div>
        <div class="schedule-warning">
          定时关机可能影响远程服务。请给任务留出收尾时间，并确认计划时间与浏览器时区一致。
        </div>

        <footer class="schedule-footer">
          <button
            v-if="scheduleConfigured"
            class="danger-outline"
            :disabled="scheduleSaving"
            @click="saveSchedule(true)"
          >
            清除计划
          </button>
          <span></span>
          <button
            class="secondary"
            :disabled="scheduleSaving"
            @click="closeSchedule()"
          >
            取消
          </button>
          <button
            class="primary"
            :disabled="scheduleSaving"
            @click="saveSchedule(false)"
          >
            {{ scheduleSaving ? "正在保存…" : "保存计划" }}
          </button>
        </footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.cloud-page {
  max-width: 1660px;
}
.partial-provider-warning {
  border-color: rgb(229 185 112 / 32%);
  color: #f0c477;
  background: rgb(229 185 112 / 9%);
}
.partial-provider-warning span {
  color: #c9a76e;
}
.partial-provider-warning button {
  color: #f0c477;
}
.heading-actions button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.cloud-summary {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr)) minmax(190px, 1.25fr);
  margin-bottom: 18px;
  border: 1px solid rgba(173, 218, 232, 0.14);
  border-radius: 14px;
  background: #0c171e;
  overflow: hidden;
}
.cloud-summary > button,
.cloud-summary > div {
  min-height: 96px;
  padding: 18px 22px;
  border: 0;
  border-right: 1px solid rgba(173, 218, 232, 0.14);
  color: #e7eaf1;
  background: transparent;
  text-align: left;
}
.cloud-summary > button {
  cursor: pointer;
}
.cloud-summary > button:hover,
.cloud-summary > button.active {
  background: rgba(80, 207, 238, 0.055);
  box-shadow: inset 0 -2px #50cfee;
}
.cloud-summary span,
.cloud-summary small {
  display: block;
  color: #858da0;
  font-size: 12px;
}
.cloud-summary strong {
  display: block;
  margin-top: 8px;
  font-size: 27px;
}
.cloud-summary .running strong {
  color: #45dfb4;
}
.cloud-summary .transitioning strong {
  color: #f0bd69;
}
.cloud-summary .issues strong {
  color: #ef8391;
}
.cloud-summary .balance {
  border-right: 0;
}
.cloud-summary .balance strong {
  color: #8ce5f8;
}
.cloud-context {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-bottom: 18px;
  padding: 14px 18px;
  border: 1px solid rgba(173, 218, 232, 0.14);
  border-radius: 12px;
  background: linear-gradient(100deg, #0c171e, rgb(17 38 48 / 72%));
}
.cloud-context > div {
  display: grid;
  gap: 3px;
}
.context-kicker {
  color: #50cfee;
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.cloud-context strong {
  color: #dce9ed;
  font-size: 13px;
}
.cloud-context p {
  flex: 1;
  margin: 0;
  color: #8d98aa;
  font-size: 12px;
  line-height: 1.55;
}
.permission-chip {
  flex: 0 0 auto;
  padding: 7px 10px;
  border: 1px solid rgb(69 223 180 / 28%);
  border-radius: 999px;
  color: #68e2c0;
  background: rgb(69 223 180 / 7%);
  font-size: 11px;
  white-space: nowrap;
}
.permission-chip.readonly {
  border-color: rgb(240 189 105 / 28%);
  color: #e5b970;
  background: rgb(240 189 105 / 7%);
}
.cloud-toolbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) 150px auto auto auto;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.cloud-search {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 40px;
  padding: 0 12px;
  border: 1px solid #30394d;
  border-radius: 9px;
  color: #778196;
  background: #101520;
}
.cloud-search input {
  width: 100%;
  border: 0;
  outline: 0;
  color: #e7eaf1;
  background: transparent;
}
.cloud-search input::placeholder {
  color: #667084;
}
.cloud-toolbar select {
  height: 40px;
  padding: 0 11px;
  border: 1px solid #30394d;
  border-radius: 9px;
  color: #dce1eb;
  background: #101520;
}
.view-switcher {
  display: inline-grid;
  grid-template-columns: 1fr 1fr;
  padding: 3px;
  border: 1px solid #30394d;
  border-radius: 9px;
  background: #0d131d;
}
.view-switcher button {
  min-width: 78px;
  height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: 6px;
  color: #7f899d;
  background: transparent;
  cursor: pointer;
}
.view-switcher button.active {
  color: #e8f7fa;
  background: #1b3440;
  box-shadow: 0 1px 7px rgb(0 0 0 / 24%);
}
.visible-count {
  color: #7f899d;
  font-size: 12px;
  white-space: nowrap;
}
.select-visible {
  height: 38px;
  padding: 0 12px;
  border: 1px solid #3a4358;
  border-radius: 8px;
  color: #b9c0ce;
  background: #151b27;
  cursor: pointer;
}
.select-visible:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.batch-bar {
  position: sticky;
  top: 86px;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  padding: 12px 16px;
  border: 1px solid rgb(80 207 238 / 34%);
  border-radius: 11px;
  background: rgb(9 24 31 / 96%);
  box-shadow: 0 10px 34px rgb(0 0 0 / 24%);
  backdrop-filter: blur(12px);
}
.batch-bar > div {
  display: flex;
  align-items: center;
  gap: 10px;
}
.batch-bar > div:first-child {
  display: grid;
  gap: 3px;
}
.batch-bar strong {
  color: #eee8fb;
}
.batch-bar span {
  color: #818a9d;
  font-size: 12px;
}
.batch-bar button {
  white-space: nowrap;
}
.resource-workspace {
  display: grid;
  gap: 26px;
}
.resource-group {
  display: grid;
  gap: 11px;
}
.resource-group-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
  padding: 0 3px;
}
.resource-group-heading > div {
  min-width: 0;
}
.resource-group-heading span {
  color: #5e9aaa;
  font-size: 10px;
  font-weight: 750;
  letter-spacing: 0.14em;
}
.resource-group-heading h2 {
  margin: 3px 0 2px;
  color: #e7edf2;
  font-size: 17px;
}
.resource-group-heading p {
  margin: 0;
  color: #737f91;
  font-size: 11px;
}
.resource-group-heading > strong {
  min-width: 36px;
  padding: 5px 10px;
  border: 1px solid #2d3a49;
  border-radius: 999px;
  color: #aeb9c6;
  background: #101820;
  font-size: 13px;
  text-align: center;
}
.group-running .resource-group-heading > strong {
  border-color: rgb(49 221 177 / 28%);
  color: #52dfb9;
  background: rgb(49 221 177 / 7%);
}
.group-attention .resource-group-heading > strong {
  border-color: rgb(225 94 109 / 28%);
  color: #ef929e;
  background: rgb(225 94 109 / 7%);
}
.instance-list {
  display: grid;
  gap: 15px;
}
.instance-card {
  border: 1px solid rgba(173, 218, 232, 0.14);
  border-left: 3px solid #596174;
  border-radius: 10px;
  background: #0c171e;
  overflow: hidden;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}
.instance-card:hover {
  border-color: rgb(123 183 201 / 30%);
  transform: translateY(-1px);
}
.instance-card.selected {
  border-color: rgb(80 207 238 / 58%);
  box-shadow:
    0 0 0 1px rgb(80 207 238 / 18%),
    0 14px 38px rgb(0 0 0 / 22%);
}
.instance-card.state-running {
  border-left-color: #28d8aa;
}
.instance-card.state-starting,
.instance-card.state-stopping,
.instance-card.state-creating {
  border-left-color: #e4aa53;
}
.instance-card.state-error,
.instance-card.state-unknown {
  border-left-color: #e15e6d;
}
.instance-head {
  display: grid;
  grid-template-columns: auto minmax(210px, 1.4fr) minmax(280px, 1fr) auto;
  align-items: center;
  gap: 14px;
  min-height: 88px;
  padding: 15px 22px;
}
.instance-selector {
  display: grid;
  place-items: center;
}
.instance-selector input {
  width: 18px;
  height: 18px;
  accent-color: #50cfee;
  cursor: pointer;
}
.instance-identity {
  display: flex;
  flex: 1;
  gap: 13px;
  min-width: 0;
}
.state-dot {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  margin-top: 22px;
  border-radius: 50%;
  background: #596174;
  box-shadow: 0 0 0 4px rgb(89 97 116 / 12%);
}
.state-running .state-dot {
  background: #31ddb1;
  box-shadow: 0 0 0 4px rgb(49 221 177 / 12%);
}
.state-starting .state-dot,
.state-stopping .state-dot,
.state-creating .state-dot {
  background: #e4aa53;
}
.instance-identity p {
  margin: 0 0 4px;
  color: #6f7890;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.instance-identity h3 {
  margin: 0 0 5px;
  font-size: 20px;
}
.instance-identity code {
  color: #7e879a;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.state-pill {
  padding: 7px 12px;
  border: 1px solid #374056;
  border-radius: 999px;
  color: #9da5b7;
  background: #181e2b;
  font-size: 12px;
  white-space: nowrap;
}
.state-column {
  display: flex;
  align-items: flex-end;
  flex-direction: column;
  gap: 7px;
}
.instance-quickfacts {
  display: grid;
  grid-template-columns: minmax(130px, 1fr) minmax(130px, 1fr);
  gap: 10px;
  min-width: 0;
}
.instance-quickfacts div {
  min-width: 0;
  padding: 9px 11px;
  border: 1px solid rgb(173 218 232 / 10%);
  border-radius: 8px;
  background: rgb(2 10 14 / 28%);
}
.instance-quickfacts span,
.instance-quickfacts strong {
  display: block;
}
.instance-quickfacts span {
  margin-bottom: 4px;
  color: #69778b;
  font-size: 10px;
}
.instance-quickfacts strong {
  color: #cfd8e0;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.actionability {
  padding: 3px 7px;
  border-radius: 5px;
  color: #9aa4b5;
  background: #161e28;
  font-size: 10px;
}
.actionability.tone-ready {
  color: #71dfc1;
  background: rgb(49 221 177 / 8%);
}
.actionability.tone-pending {
  color: #e5b970;
  background: rgb(229 185 112 / 8%);
}
.actionability.tone-blocked,
.actionability.tone-readonly {
  color: #8993a4;
}
.operation-state {
  color: #e2b567;
  font-size: 10px;
}
.provider-state {
  max-width: 220px;
  color: #737d91;
  font-size: 10px;
  overflow-wrap: anywhere;
  text-align: right;
}
.state-running .state-pill {
  border-color: rgb(49 221 177 / 30%);
  color: #48dfb8;
  background: rgb(49 221 177 / 8%);
}
.instance-details {
  border-top: 1px solid rgba(173, 218, 232, 0.1);
  background: rgb(3 10 15 / 18%);
}
.detail-heading {
  display: flex;
  align-items: baseline;
  gap: 9px;
  padding: 14px 22px 0 58px;
}
.detail-heading span {
  color: #b7c2ce;
  font-size: 11px;
  font-weight: 720;
}
.detail-heading small {
  color: #697487;
  font-size: 10px;
}
.detail-heading.control-heading {
  padding-top: 2px;
}
.instance-body {
  display: grid;
  grid-template-columns: minmax(190px, 1.1fr) minmax(380px, 2fr) minmax(
      170px,
      0.9fr
    );
  gap: 24px;
  padding: 12px 22px 18px 58px;
  align-items: center;
}
.primary-spec span,
.application span {
  display: block;
  margin-bottom: 6px;
  color: #747e93;
  font-size: 12px;
}
.primary-spec strong,
.application strong {
  display: block;
  color: #f0f2f7;
  font-size: 15px;
}
.primary-spec small,
.application small {
  display: block;
  margin-top: 6px;
  color: #7d8699;
}
.instance-body dl {
  display: grid;
  grid-template-columns: repeat(4, minmax(82px, 1fr));
  gap: 12px;
  margin: 0;
}
.instance-body dl div {
  min-width: 0;
}
.instance-body dt {
  color: #747e93;
  font-size: 11px;
}
.instance-body dd {
  margin: 6px 0 0;
  color: #e1e4eb;
  font-weight: 650;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.control-evidence {
  display: grid;
  grid-template-columns: 1.25fr 1fr 1.2fr 0.9fr 1fr;
  margin: 10px 22px 16px 58px;
  border: 1px solid rgba(173, 218, 232, 0.14);
  border-radius: 9px;
  background: rgba(3, 12, 17, 0.32);
  overflow: hidden;
}
.control-evidence section {
  min-width: 0;
  padding: 11px 13px;
  border-right: 1px solid rgba(173, 218, 232, 0.14);
}
.control-evidence section:last-child {
  border-right: 0;
}
.control-evidence span,
.control-evidence small {
  display: block;
  color: #717b8e;
  font-size: 10px;
}
.schedule-state small + small {
  margin-top: 3px;
}
.control-evidence strong {
  display: block;
  margin: 5px 0;
  color: #dce1eb;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.control-evidence .healthy strong {
  color: #46dbb4;
}
.control-evidence .warning strong,
.current-step strong {
  color: #e5b970;
}
.snapshot-warning,
.operation-error {
  margin: 12px 22px;
  padding: 8px 11px;
  border-radius: 7px;
  font-size: 12px;
}
.snapshot-warning {
  color: #e5b970;
  background: rgb(229 185 112 / 8%);
}
.operation-error {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #f0a2ad;
  background: rgb(225 94 109 / 9%);
}
.operation-error span {
  flex: 1;
}
.operation-error button {
  border: 0;
  color: #eeb4bd;
  background: transparent;
  text-decoration: underline;
  cursor: pointer;
}
.instance-card > footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
  min-height: 61px;
  padding: 11px 22px 11px 58px;
  border-top: 1px solid rgba(173, 218, 232, 0.14);
  background: rgba(4, 14, 19, 0.45);
}
.access-actions,
.power-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 9px;
}
.text-action {
  padding: 6px 8px;
  border: 0;
  color: #50cfee;
  background: transparent;
  cursor: pointer;
}
.detail-toggle {
  padding: 7px 10px;
  border: 1px solid #344152;
  border-radius: 7px;
  color: #aab6c3;
  background: #111a23;
  cursor: pointer;
}
.view-compact {
  gap: 20px;
}
.view-compact .resource-group {
  gap: 8px;
}
.view-compact .instance-list {
  gap: 7px;
}
.view-compact .instance-card {
  border-left-width: 2px;
  border-radius: 8px;
}
.view-compact .instance-card:hover {
  transform: none;
}
.view-compact .instance-head {
  min-height: 64px;
  padding-block: 9px;
}
.view-compact .instance-identity h3 {
  margin-bottom: 2px;
  font-size: 15px;
}
.view-compact .instance-identity p,
.view-compact .provider-state {
  display: none;
}
.view-compact .state-dot {
  margin-top: 8px;
}
.view-compact .instance-quickfacts div {
  padding-block: 6px;
  border-color: transparent;
  background: transparent;
}
.view-compact .instance-card > footer {
  min-height: 49px;
  padding-block: 7px;
}
.text-action:hover {
  color: #9ce9f9;
}
.power-actions button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.danger-outline {
  padding: 9px 15px;
  border: 1px solid #70424b;
  border-radius: 8px;
  color: #e3949f;
  background: transparent;
  cursor: pointer;
}
.danger-outline:hover {
  border-color: #b85765;
  background: rgb(184 87 101 / 9%);
}
.danger-outline:disabled,
.power-actions button:disabled {
  opacity: 0.45;
  cursor: wait;
}
.state-note {
  color: #7f8799;
  font-size: 12px;
}
.ssh-modal {
  width: min(610px, calc(100vw - 32px));
  border: 1px solid rgba(173, 218, 232, 0.24);
  border-radius: 11px;
  background: #0c1920;
  box-shadow: 0 28px 90px rgb(0 0 0 / 55%);
  overflow: hidden;
}
.ssh-modal header {
  display: flex;
  justify-content: space-between;
  padding: 22px 24px 16px;
  border-bottom: 1px solid rgba(173, 218, 232, 0.14);
}
.ssh-modal h2 {
  margin: 3px 0;
}
.ssh-modal header p {
  margin: 0;
  color: #7f889b;
}
.modal-eyebrow {
  color: #50cfee;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.credential-warning {
  margin: 18px 24px 0;
  padding: 11px 13px;
  border: 1px solid #66582f;
  border-radius: 8px;
  color: #ddc57c;
  background: rgb(147 118 36 / 10%);
  font-size: 12px;
}
.credentials-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin: 18px 24px;
}
.credentials-grid > div {
  padding: 13px;
  border: 1px solid #283146;
  border-radius: 9px;
  background: #0d121c;
  min-width: 0;
}
.credentials-grid dt {
  color: #747e93;
  font-size: 11px;
}
.credentials-grid dd {
  margin: 6px 0 0;
  color: #eef0f6;
  overflow-wrap: anywhere;
}
.credentials-grid .password-row {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 8px;
}
.password-row dt {
  grid-column: 1 / -1;
}
.password-row dd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.command-box {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 0 24px;
  padding: 12px;
  border-radius: 9px;
  background: #090e17;
}
.command-box code {
  flex: 1;
  color: #c6cfdd;
  overflow-wrap: anywhere;
}
.command-box button {
  border: 0;
  color: #50cfee;
  background: transparent;
  cursor: pointer;
  white-space: nowrap;
}
.ssh-modal > footer {
  display: flex;
  justify-content: flex-end;
  padding: 18px 24px 22px;
}
.schedule-timezone {
  display: grid;
  gap: 4px;
  margin: 18px 24px 0;
  padding: 11px 13px;
  border: 1px solid rgb(80 207 238 / 26%);
  border-radius: 8px;
  color: #a8eaf7;
  background: rgb(80 207 238 / 7%);
  font-size: 12px;
}
.schedule-timezone span {
  color: #8d96a8;
}
.schedule-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin: 18px 24px;
}
.schedule-fields label {
  display: grid;
  gap: 7px;
  min-width: 0;
  color: #dfe3ec;
  font-size: 13px;
}
.schedule-fields input {
  width: 100%;
  height: 42px;
  box-sizing: border-box;
  padding: 0 11px;
  border: 1px solid #343e53;
  border-radius: 8px;
  outline: 0;
  color: #eef0f6;
  color-scheme: dark;
  background: #0c111a;
}
.schedule-fields input:focus {
  border-color: #50cfee;
  box-shadow: 0 0 0 2px rgb(80 207 238 / 10%);
}
.schedule-fields input:disabled {
  opacity: 0.55;
}
.schedule-fields small {
  color: #747e91;
  font-size: 11px;
  line-height: 1.45;
}
.schedule-error,
.schedule-warning {
  margin: 0 24px 12px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.5;
}
.schedule-error {
  color: #f3a2ad;
  background: rgb(225 94 109 / 10%);
}
.schedule-warning {
  color: #d8be75;
  background: rgb(156 123 38 / 9%);
}
.ssh-modal > .schedule-footer {
  display: grid;
  grid-template-columns: auto 1fr auto auto;
  align-items: center;
  gap: 9px;
}
.schedule-footer button:disabled,
.icon-button:disabled {
  opacity: 0.45;
  cursor: wait;
}
@media (max-width: 1100px) {
  .cloud-summary {
    grid-template-columns: repeat(3, 1fr);
  }
  .cloud-summary .balance {
    grid-column: auto;
  }
  .cloud-toolbar {
    grid-template-columns: minmax(220px, 1fr) 140px auto;
  }
  .visible-count {
    grid-column: 1 / 3;
  }
  .select-visible {
    grid-column: 3;
    width: fit-content;
    justify-self: end;
  }
  .instance-head {
    grid-template-columns: auto minmax(200px, 1fr) auto;
  }
  .instance-quickfacts {
    grid-column: 2 / -1;
    grid-row: 2;
  }
  .instance-body {
    grid-template-columns: 1fr 2fr;
  }
  .application {
    grid-column: 1 / -1;
  }
  .control-evidence {
    grid-template-columns: repeat(3, 1fr);
  }
  .control-evidence section:nth-child(3) {
    border-right: 0;
  }
  .control-evidence section:nth-child(n + 4) {
    border-top: 1px solid #252e40;
  }
}
@media (max-width: 760px) {
  .cloud-summary {
    grid-template-columns: repeat(2, 1fr);
  }
  .cloud-summary .balance {
    grid-column: span 2;
  }
  .cloud-context {
    align-items: stretch;
    flex-direction: column;
    gap: 9px;
  }
  .permission-chip {
    width: fit-content;
  }
  .cloud-toolbar {
    grid-template-columns: 1fr;
  }
  .cloud-toolbar select,
  .view-switcher,
  .select-visible {
    width: 100%;
  }
  .view-switcher button {
    min-width: 0;
  }
  .select-visible {
    grid-column: auto;
    justify-self: stretch;
  }
  .visible-count {
    order: 3;
    grid-column: auto;
  }
  .batch-bar,
  .batch-bar > div:last-child {
    align-items: stretch;
    flex-direction: column;
  }
  .batch-bar {
    top: 82px;
  }
  .resource-group-heading {
    align-items: center;
  }
  .resource-group-heading p {
    display: none;
  }
  .instance-head {
    grid-template-columns: auto minmax(0, 1fr) auto;
    padding-inline: 18px;
  }
  .instance-selector {
    padding-top: 22px;
  }
  .state-column {
    width: auto;
    align-items: flex-end;
    padding-left: 0;
  }
  .provider-state {
    display: none;
  }
  .instance-quickfacts {
    grid-column: 2 / -1;
    grid-row: 2;
  }
  .instance-body {
    grid-template-columns: 1fr;
    padding: 8px 18px 16px 18px;
  }
  .instance-body dl {
    grid-template-columns: repeat(2, 1fr);
  }
  .application {
    grid-column: auto;
  }
  .control-evidence {
    grid-template-columns: 1fr;
    margin-inline: 18px;
  }
  .control-evidence section {
    border-right: 0;
    border-top: 1px solid #252e40;
  }
  .control-evidence section:first-child {
    border-top: 0;
  }
  .snapshot-warning,
  .operation-error {
    margin-inline: 18px;
  }
  .operation-error {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .instance-card > footer {
    align-items: stretch;
    flex-direction: column;
    padding-inline: 18px;
  }
  .detail-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
    padding-inline: 18px;
  }
  .view-compact .instance-quickfacts {
    display: none;
  }
  .view-compact .instance-head {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }
  .view-compact .instance-card > footer {
    padding-inline: 14px;
  }
  .power-actions {
    justify-content: stretch;
  }
  .power-actions button {
    flex: 1;
    justify-content: center;
  }
  .credentials-grid {
    grid-template-columns: 1fr;
  }
  .schedule-fields {
    grid-template-columns: 1fr;
  }
  .ssh-modal > .schedule-footer {
    grid-template-columns: 1fr 1fr;
  }
  .schedule-footer > span {
    display: none;
  }
  .schedule-footer .danger-outline {
    grid-column: 1 / -1;
  }
  .command-box {
    align-items: stretch;
    flex-direction: column;
  }
  .command-box button {
    align-self: flex-end;
  }
}
@media (max-width: 480px) {
  .cloud-summary {
    grid-template-columns: 1fr;
  }
  .cloud-summary .balance {
    grid-column: auto;
  }
  .cloud-summary > button,
  .cloud-summary > div {
    min-height: 74px;
    padding: 13px 16px;
    border-right: 0;
    border-bottom: 1px solid #273044;
  }
  .cloud-summary .balance {
    border-bottom: 0;
  }
  .instance-identity h3 {
    font-size: 17px;
  }
  .instance-head {
    gap: 9px;
    padding-inline: 13px;
  }
  .state-column .actionability {
    display: none;
  }
  .instance-quickfacts {
    grid-template-columns: 1fr;
  }
  .instance-card > footer {
    padding-inline: 13px;
  }
  .access-actions,
  .power-actions {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .access-actions button,
  .power-actions button {
    justify-content: center;
    width: 100%;
  }
  .instance-body dl {
    grid-template-columns: 1fr 1fr;
  }
  .credentials-grid .password-row {
    grid-template-columns: 1fr auto;
  }
  .password-row dd {
    grid-column: 1 / -1;
  }
}
</style>
