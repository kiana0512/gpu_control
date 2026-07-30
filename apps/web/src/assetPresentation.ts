import type { AssetJobInfo } from "./types";

export interface AssetDeliveryPolicy {
  label: string;
  className: "ready" | "warning" | "blocked";
}

function hasQaWarning(job: AssetJobInfo): boolean {
  const warning = job.options.qa_warning;
  return warning !== undefined && warning !== null;
}

export function assetDeliveryPolicy(job: AssetJobInfo): AssetDeliveryPolicy {
  if (job.delivery_ready) {
    if (job.status === "SUCCEEDED" && hasQaWarning(job)) {
      return { label: "已交付 · QA 告警", className: "warning" };
    }
    return { label: "质量检查通过", className: "ready" };
  }
  if (job.artifacts_role === "diagnostic") {
    return { label: "质量检查未通过", className: "blocked" };
  }
  return { label: "正在生成交付", className: "blocked" };
}
