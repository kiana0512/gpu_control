import { formatGpuPower, formatGpuTemperature } from "../src/nodePresentation";
import type { NodeInfo } from "../src/types";

function node(overrides: Partial<NodeInfo> = {}): NodeInfo {
  return {
    id: "worker-3090-a",
    display_name: "3090-A",
    base_url: "http://10.3.34.12:8188",
    pool: "PRIMARY",
    mode: "ACTIVE",
    health: "ONLINE",
    current_jobs: 1,
    max_concurrency: 1,
    gpu_util_percent: 100,
    gpu_temperature_c: 68,
    gpu_power_w: 301.4,
    free_vram_mb: 12000,
    total_vram_mb: 24576,
    manual_reserved: false,
    foreign_queue_detected: false,
    ...overrides,
  };
}

describe("GPU node telemetry presentation", () => {
  it("formats live temperature and power with compact units", () => {
    const value = node();
    expect(formatGpuTemperature(value)).toBe("68 °C");
    expect(formatGpuPower(value)).toBe("301 W");
  });

  it("does not present missing, invalid, or offline values as live telemetry", () => {
    expect(formatGpuTemperature(node({ gpu_temperature_c: null }))).toBe("—");
    expect(formatGpuPower(node({ gpu_power_w: Number.NaN }))).toBe("—");
    expect(formatGpuTemperature(node({ health: "OFFLINE" }))).toBe("—");
  });
});
