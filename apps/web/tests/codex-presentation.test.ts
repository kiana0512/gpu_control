import {
  codexAdmissionLabel,
  codexHealthLabel,
  codexHealthMessage,
  codexRuntimeState,
  healthyCodexProbeAverage,
} from "../src/codexPresentation";
import type { NodeInfo } from "../src/types";

const now = Date.parse("2026-09-11T08:00:00Z");
function node(
  overrides: Partial<NonNullable<NodeInfo["codex_cli"]>> = {},
  mode: NodeInfo["mode"] = "ACTIVE",
) {
  return {
    mode,
    codex_cli: {
      health: "HEALTHY",
      host_entry_installed: true,
      host_version: "codex-cli 0.146.0",
      runtime_version: "codex-cli 0.146.0",
      auth_status: "AUTHENTICATED",
      probe_status: "HEALTHY",
      probe_latency_ms: 12000,
      last_checked_at: new Date(now).toISOString(),
      last_success_at: new Date(now).toISOString(),
      worker_status: "ONLINE",
      worker_last_heartbeat_at: new Date(now).toISOString(),
      heartbeat_fresh: true,
      probe_fresh: true,
      eligibility_reason: "ELIGIBLE",
      error_code: null,
      task: null,
      scheduler_eligible: true,
      ...overrides,
    } as NonNullable<NodeInfo["codex_cli"]>,
  };
}

describe("Codex runtime presentation", () => {
  it("shows newly registered nodes without suggesting CLI installation proves authorization", () => {
    expect(codexRuntimeState({}, now).key).toBe("unregistered");
    const missing = node({
      auth_status: "MISSING",
      probe_status: "BLOCKED",
      error_code: "AUTH_MISSING",
      health: "DEGRADED",
    });
    expect(codexHealthLabel(missing, now)).toBe("尚未授权");
    expect(codexRuntimeState(missing, now).healthy).toBe(false);
    expect(
      codexRuntimeState(
        node({ auth_status: "PRESENT", probe_status: "NOT_RUN" }),
        now,
      ).healthy,
    ).toBe(false);
  });
  it.each([
    "AUTH_REFRESH_REUSED",
    "AUTH_UNAUTHORIZED",
    "REAUTH_REQUIRED",
    "PROBE_UNAUTHORIZED",
  ])("distinguishes expired authorization %s", (error_code) => {
    expect(codexRuntimeState(node({ error_code }), now).key).toBe(
      "auth_expired",
    );
  });
  it("keeps unreadable legacy credentials separate from confirmed expiry", () => {
    expect(
      codexRuntimeState(
        node({ auth_status: "INVALID", error_code: "AUTH_INVALID" }),
        now,
      ).key,
    ).toBe("auth_invalid");
  });
  it("explains a failed real call instead of showing a historical healthy result", () => {
    const failed = node({
      probe_status: "FAILED",
      error_code: "PROBE_TIMEOUT",
    });
    expect(codexHealthMessage(failed, now)).toContain("真实 exec 探针超时");
    expect(codexRuntimeState(failed, now).key).toBe("probe_failed");
  });
  it("expires cached healthy data as time advances even if the API is unavailable", () => {
    expect(codexRuntimeState(node(), now).healthy).toBe(true);
    expect(codexRuntimeState(node(), now + 30_001).key).toBe("heartbeat_stale");
    expect(
      codexRuntimeState(node({ heartbeat_timeout_seconds: 60 }), now + 40_000)
        .healthy,
    ).toBe(true);
    expect(
      codexRuntimeState(node({ worker_last_heartbeat_at: "invalid" }), now).key,
    ).toBe("heartbeat_stale");
  });
  it("separately expires probe results and respects backend freshness rejection", () => {
    expect(codexRuntimeState(node({ heartbeat_fresh: false }), now).key).toBe(
      "heartbeat_stale",
    );
    expect(codexRuntimeState(node({ probe_fresh: false }), now).key).toBe(
      "probe_stale",
    );
    expect(
      codexRuntimeState(
        node({ last_checked_at: new Date(now - 3600_001).toISOString() }),
        now,
      ).key,
    ).toBe("probe_stale");
    expect(
      codexRuntimeState(
        node({
          probe_max_age_seconds: 60,
          last_checked_at: new Date(now - 61_000).toISOString(),
        }),
        now,
      ).key,
    ).toBe("probe_stale");
  });
  it.each(["DISABLED", "DRAINING", "RESERVED"] as const)(
    "separates runtime health from %s node admission",
    (mode) => {
      const item = node({}, mode);
      expect(codexRuntimeState(item, now).healthy).toBe(true);
      expect(codexAdmissionLabel(item, now)).not.toBe("可领取 Codex 任务");
    },
  );
  it("averages only fresh healthy finite latency measurements", () => {
    expect(
      healthyCodexProbeAverage(
        [
          node(),
          node({ probe_latency_ms: 16000 }),
          node({ error_code: "PROBE_FAILED" }),
          node({ heartbeat_fresh: false }),
          node({ probe_latency_ms: NaN }),
          node({ probe_latency_ms: -1 }),
        ],
        now,
      ),
    ).toBe(14000);
  });
});
