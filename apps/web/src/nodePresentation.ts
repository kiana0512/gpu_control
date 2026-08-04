import type { NodeInfo } from "./types";

function onlineMetric(node: NodeInfo, value: number | null): number | null {
  return node.health === "ONLINE" && value !== null && Number.isFinite(value)
    ? value
    : null;
}

export function formatGpuTemperature(node: NodeInfo): string {
  const value = onlineMetric(node, node.gpu_temperature_c);
  return value === null ? "—" : `${Math.round(value)} °C`;
}

export function formatGpuPower(node: NodeInfo): string {
  const value = onlineMetric(node, node.gpu_power_w);
  return value === null ? "—" : `${Math.round(value)} W`;
}
