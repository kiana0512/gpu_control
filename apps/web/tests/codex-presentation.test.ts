import {
  codexHealthLabel,
  codexHealthMessage,
  healthyCodexProbeAverage,
} from "../src/codexPresentation";
import type { NodeInfo } from "../src/types";

function codexNode(
  health: NonNullable<NodeInfo["codex_cli"]>["health"],
  probeStatus: string,
  latency: number | null,
  errorCode: string | null = null,
  schedulerEligible = health === "HEALTHY",
  eligibilityReason = schedulerEligible ? "ELIGIBLE" : "CODEX_PROBE_UNHEALTHY",
): Pick<NodeInfo, "codex_cli"> {
  return {
    codex_cli: {
      health,
      host_entry_installed: true,
      host_version: "codex-cli 0.146.0-alpha.3.1",
      runtime_version: "codex-cli 0.146.0-alpha.3.1",
      auth_status: "AUTHENTICATED",
      probe_status: probeStatus,
      probe_latency_ms: latency,
      last_checked_at: "2026-08-03T08:00:00Z",
      last_success_at: health === "HEALTHY" ? "2026-08-03T08:00:00Z" : null,
      worker_status: "ONLINE",
      worker_last_heartbeat_at: "2026-08-03T08:00:00Z",
      heartbeat_fresh: eligibilityReason !== "ASSET_WORKER_HEARTBEAT_STALE",
      probe_fresh: eligibilityReason !== "CODEX_PROBE_STALE",
      eligibility_reason: eligibilityReason,
      error_code: errorCode,
      task: null,
      scheduler_eligible: schedulerEligible,
    },
  };
}

describe("Codex runtime presentation", () => {
  it("computes latency from healthy probes only", () => {
    expect(
      healthyCodexProbeAverage([
        codexNode("DEGRADED", "FAILED", 7807, "PROBE_FAILED"),
        codexNode("HEALTHY", "HEALTHY", 13834),
        codexNode("DEGRADED", "FAILED", 13844, "AUTH_REFRESH_REUSED"),
        codexNode("HEALTHY", "HEALTHY", 99999, null, false),
      ]),
    ).toBe(13834);
  });

  it("explains credential recovery without implying unrelated queues are blocked", () => {
    expect(
      codexHealthMessage(
        codexNode("DEGRADED", "FAILED", 8000, "AUTH_REFRESH_REUSED"),
      ),
    ).toBe(
      "登录凭证已失效，需要通过安全渠道重新登录。本 Worker 不领取 Codex 重拓扑；其他资产队列不因该 Codex 状态被门禁。",
    );
    expect(
      codexHealthMessage(
        codexNode("DEGRADED", "FAILED", 90000, "PROBE_TIMEOUT"),
      ),
    ).toContain("真实 exec 探针超时");
  });

  it("labels stale workers and explains which freshness evidence expired", () => {
    const heartbeatStale = codexNode(
      "STALE",
      "HEALTHY",
      7000,
      null,
      false,
      "ASSET_WORKER_HEARTBEAT_STALE",
    );
    const probeStale = codexNode(
      "STALE",
      "HEALTHY",
      12000,
      null,
      false,
      "CODEX_PROBE_STALE",
    );

    expect(codexHealthLabel(heartbeatStale)).toBe("状态已过期");
    expect(codexHealthMessage(heartbeatStale)).toContain(
      "Linux Asset Worker 心跳已过期",
    );
    expect(codexHealthMessage(probeStale)).toContain(
      "Codex 真实 exec 探针已超过有效期",
    );
    expect(healthyCodexProbeAverage([heartbeatStale, probeStale])).toBeNull();
  });
});
