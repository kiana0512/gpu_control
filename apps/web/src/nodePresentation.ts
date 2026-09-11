import type { NodeInfo } from "./types";

function liveAgentMetric(node: NodeInfo, value: number | null): number | null {
  return node.health !== "OFFLINE" && value !== null && Number.isFinite(value)
    ? value
    : null;
}

export function formatGpuTemperature(node: NodeInfo): string {
  const value = liveAgentMetric(node, node.gpu_temperature_c);
  return value === null ? "—" : `${Math.round(value)} °C`;
}

export function formatGpuPower(node: NodeInfo): string {
  const value = liveAgentMetric(node, node.gpu_power_w);
  return value === null ? "—" : `${Math.round(value)} W`;
}

// Keep controllers first, then naturally sort every registered worker, including future nodes.
export function compareNodes(
  left: { id: string; display_name?: string; name?: string },
  right: { id: string; display_name?: string; name?: string },
) {
  return (
    Number(right.id.startsWith("control-")) -
      Number(left.id.startsWith("control-")) ||
    (left.display_name ?? left.name ?? left.id).localeCompare(
      right.display_name ?? right.name ?? right.id,
      "zh-CN",
      { numeric: true },
    ) ||
    left.id.localeCompare(right.id, "en", { numeric: true })
  );
}

export function validatedVramSummary(
  node: Pick<NodeInfo, "id" | "labels">,
): string | null {
  const raw = node.labels?.validated_vram_profiles;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const profiles = Object.values(raw).filter((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry))
      return false;
    const profile = entry as Record<string, unknown>;
    return (
      profile.status === "PASSED" &&
      profile.node_id === node.id &&
      typeof profile.version === "string" &&
      typeof profile.min_vram_mb === "number" &&
      Number.isFinite(profile.min_vram_mb) &&
      profile.min_vram_mb > 0
    );
  }) as Array<{ min_vram_mb: number }>;
  if (!profiles.length) return null;
  const limits = [
    ...new Set(profiles.map((profile) => `${profile.min_vram_mb / 1000} GB`)),
  ].join(" / ");
  return `专项验收 ${profiles.length} 个工作流版本 / ${limits}；实际接单以后端兼容性校验为准`;
}
