import type { NodeInfo } from "./types";

type CodexNode = Pick<NodeInfo, "codex_cli">;

export function healthyCodexProbeAverage(nodes: CodexNode[]) {
  const values = nodes
    .filter(
      (node) =>
        node.codex_cli?.scheduler_eligible === true &&
        node.codex_cli?.health === "HEALTHY" &&
        node.codex_cli.probe_status === "HEALTHY",
    )
    .map((node) => node.codex_cli?.probe_latency_ms)
    .filter((value): value is number => value != null);
  return values.length
    ? Math.round(values.reduce((sum, value) => sum + value, 0) / values.length)
    : null;
}

export function codexHealthLabel(node: CodexNode) {
  const health = node.codex_cli?.health ?? "CHECKING";
  return {
    HEALTHY: "真实调用正常",
    CHECKING: "等待探针",
    DEGRADED: "调用异常",
    STALE: "状态已过期",
    UNAVAILABLE: "当前不可用",
  }[health];
}

export function codexHealthMessage(node: CodexNode) {
  const runtime = node.codex_cli;
  if (!runtime) return "节点尚未上报 Codex CLI 状态";
  if (runtime.health === "HEALTHY" && runtime.scheduler_eligible)
    return `认证有效，真实 exec 探针已通过${runtime.probe_latency_ms == null ? "" : ` · ${runtime.probe_latency_ms} ms`}`;
  if (runtime.health === "HEALTHY")
    return "真实 exec 探针已通过，但 Worker 调度资格尚未同步，请等待下一次心跳。";
  if (runtime.health === "CHECKING") return "已发现命令，正在核验登录与调用";
  if (runtime.health === "STALE") {
    if (runtime.eligibility_reason === "ASSET_WORKER_HEARTBEAT_STALE")
      return "Linux Asset Worker 心跳已过期，当前不可领取 Codex 重拓扑任务。";
    if (runtime.eligibility_reason === "CODEX_PROBE_STALE")
      return "Codex 真实 exec 探针已超过有效期，当前不可领取重拓扑任务；等待下一次健康探针。";
    return "Codex 调度资格证据已过期，当前不可领取重拓扑任务。";
  }
  if (runtime.health === "UNAVAILABLE") {
    if (runtime.eligibility_reason === "ASSET_WORKER_NOT_REGISTERED")
      return "Linux Asset Worker 尚未注册，当前不可领取 Codex 重拓扑任务。";
    if (runtime.eligibility_reason === "ASSET_WORKER_OFFLINE")
      return "Linux Asset Worker 已离线，当前不可领取 Codex 重拓扑任务。";
    return "Codex 运行时当前不可用，无法领取重拓扑任务。";
  }

  const unaffected =
    "本 Worker 不领取 Codex 重拓扑；其他资产队列不因该 Codex 状态被门禁。";
  if (
    [
      "AUTH_REFRESH_REUSED",
      "AUTH_UNAUTHORIZED",
      "REAUTH_REQUIRED",
      "PROBE_UNAUTHORIZED",
    ].includes(runtime.error_code ?? "")
  )
    return `登录凭证已失效，需要通过安全渠道重新登录。${unaffected}`;
  if (runtime.error_code === "PROBE_TIMEOUT")
    return `真实 exec 探针超时。${unaffected}`;
  return `真实 exec 探针未通过${runtime.error_code ? `（${runtime.error_code}）` : ""}。${unaffected}`;
}
