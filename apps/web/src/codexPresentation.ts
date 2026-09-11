import type { NodeInfo } from "./types";

type CodexNode = Pick<NodeInfo, "codex_cli"> &
  Partial<Pick<NodeInfo, "mode" | "manual_reserved">>;
type Tone = "healthy" | "checking" | "degraded" | "stale" | "unavailable";

function freshTimestamp(
  value: string | null,
  seconds: number | undefined,
  fallback: number,
  now: number,
) {
  const ttl =
    seconds != null && Number.isFinite(seconds) && seconds > 0
      ? seconds
      : fallback;
  const age = value ? now - Date.parse(value) : NaN;
  return Number.isFinite(age) && age >= -30_000 && age <= ttl * 1000;
}

export function codexRuntimeState(node: CodexNode, now = Date.now()) {
  const runtime = node.codex_cli;
  const state = (
    key: string,
    label: string,
    message: string,
    tone: Tone,
    authenticated = false,
  ) => ({
    key,
    label,
    message,
    tone,
    healthy: key === "healthy",
    authenticated,
  });
  if (!runtime || runtime.eligibility_reason === "ASSET_WORKER_NOT_REGISTERED")
    return state(
      "unregistered",
      "等待 Worker 接入",
      "节点已登记，尚未收到 Linux Asset Worker 的 Codex 运行状态。",
      "checking",
    );
  if (runtime.worker_status && runtime.worker_status !== "ONLINE")
    return state(
      "offline",
      "Worker 离线",
      "Linux Asset Worker 已离线，历史探针结果不能证明当前可调用。",
      "unavailable",
    );
  if (
    !runtime.heartbeat_fresh ||
    !freshTimestamp(
      runtime.worker_last_heartbeat_at,
      runtime.heartbeat_timeout_seconds,
      30,
      now,
    )
  )
    return state(
      "heartbeat_stale",
      "心跳已过期",
      "Linux Asset Worker 心跳已过期，等待新心跳确认当前运行状态。",
      "stale",
    );
  const code = runtime.error_code ?? "";
  const auth = runtime.auth_status;
  if (code === "BINARY_UNAVAILABLE")
    return state(
      "not_installed",
      "CLI 不可用",
      "Worker 尚未报告可执行的 Codex CLI。",
      "unavailable",
    );
  if (
    auth === "EXPIRED" ||
    [
      "AUTH_REFRESH_REUSED",
      "AUTH_UNAUTHORIZED",
      "REAUTH_REQUIRED",
      "PROBE_UNAUTHORIZED",
    ].includes(code)
  )
    return state(
      "auth_expired",
      "授权已失效",
      "登录凭证已失效，需要通过安全渠道重新登录并通过真实调用探针。",
      "degraded",
    );
  if (
    [
      "MISSING",
      "ABSENT",
      "UNAUTHORIZED",
      "UNAUTHENTICATED",
      "NOT_CONFIGURED",
    ].includes(auth) ||
    ["AUTH_MISSING", "AUTH_NOT_FOUND", "AUTH_SOURCE_MISSING"].includes(code)
  )
    return state(
      "unauthorized",
      "尚未授权",
      "尚未配置可用的 Codex 授权；安装 CLI 后仍需登录并验证真实调用。",
      "checking",
    );
  if (auth === "INVALID" || code === "AUTH_INVALID")
    return state(
      "auth_invalid",
      "授权不可用",
      "凭证无效或无法读取，尚未完成认证验证。",
      "degraded",
    );
  if (
    code ||
    runtime.probe_status === "FAILED" ||
    runtime.eligibility_reason === "CODEX_PROBE_UNHEALTHY"
  ) {
    const messages: Record<string, string> = {
      PROBE_TIMEOUT: "真实 exec 探针超时，等待后续探针确认恢复。",
      NETWORK_TIMEOUT: "探针网络请求超时，等待后续探针确认恢复。",
      NETWORK_TLS: "探针 TLS 连接失败，尚未验证真实调用。",
      RATE_LIMITED: "探针受到请求限流，等待后续探针确认恢复。",
      SKILL_MOUNT_INVALID: "探针所需技能目录不可用。",
      PROBE_OUTPUT_MISMATCH: "真实 exec 探针返回结果未通过校验。",
      VERSION_FAILED: "无法读取 Worker CLI 版本，尚未验证真实调用。",
    };
    return state(
      "probe_failed",
      "探针失败",
      messages[code] ?? `真实 exec 探针未通过${code ? `（${code}）` : ""}。`,
      "degraded",
    );
  }
  if (
    ["NOT_RUN", "BLOCKED", "RECOVERY_PENDING", "CHECKING", "RUNNING"].includes(
      runtime.probe_status,
    )
  )
    return state(
      "checking",
      "等待探针",
      "凭证与 CLI 已报告，等待真实 exec 调用验证。",
      "checking",
    );
  if (
    !runtime.probe_fresh ||
    !freshTimestamp(
      runtime.last_checked_at,
      runtime.probe_max_age_seconds,
      3600,
      now,
    )
  )
    return state(
      "probe_stale",
      "探针已过期",
      "Codex 真实 exec 探针已超过有效期，等待下一次健康探针。",
      "stale",
    );
  if (
    auth === "AUTHENTICATED" &&
    runtime.probe_status === "HEALTHY" &&
    runtime.health === "HEALTHY"
  )
    return state(
      "healthy",
      "真实调用正常",
      `认证有效，真实 exec 探针已通过${runtime.probe_latency_ms == null ? "" : ` · ${runtime.probe_latency_ms} ms`}`,
      "healthy",
      true,
    );
  return state(
    "checking",
    "等待验证",
    "尚未收到完整且有效的认证与真实调用证据。",
    "checking",
  );
}

export function codexHealthLabel(node: CodexNode, now = Date.now()) {
  return codexRuntimeState(node, now).label;
}
export function codexHealthMessage(node: CodexNode, now = Date.now()) {
  return codexRuntimeState(node, now).message;
}
export function codexAdmissionLabel(node: CodexNode, now = Date.now()) {
  if (node.mode === "DISABLED") return "节点已禁用";
  if (node.mode === "DRAINING") return "节点排空中";
  if (node.mode === "RESERVED" || node.manual_reserved) return "节点已保留";
  return codexRuntimeState(node, now).healthy &&
    node.codex_cli?.scheduler_eligible
    ? "可领取 Codex 任务"
    : "等待运行时就绪";
}
export function codexAuthLabel(value?: string) {
  return (
    (
      {
        AUTHENTICATED: "认证已验证",
        PRESENT: "凭证已配置",
        MISSING: "尚未授权",
        ABSENT: "尚未授权",
        INVALID: "凭证不可用",
        EXPIRED: "授权已失效",
        UNAVAILABLE: "尚不可用",
      } as Record<string, string>
    )[value ?? ""] ??
    value ??
    "待上报"
  );
}
export function codexProbeLabel(value?: string) {
  return (
    (
      {
        HEALTHY: "调用通过",
        FAILED: "调用失败",
        NOT_RUN: "尚未执行",
        BLOCKED: "等待前置条件",
        RECOVERY_PENDING: "等待复检",
        UNAVAILABLE: "尚不可用",
      } as Record<string, string>
    )[value ?? ""] ??
    value ??
    "待上报"
  );
}
export function healthyCodexProbeAverage(nodes: CodexNode[], now = Date.now()) {
  const values = nodes
    .filter((node) => codexRuntimeState(node, now).healthy)
    .map((node) => node.codex_cli?.probe_latency_ms)
    .filter(
      (value): value is number =>
        value != null && Number.isFinite(value) && value >= 0,
    );
  return values.length
    ? Math.round(values.reduce((sum, value) => sum + value, 0) / values.length)
    : null;
}
