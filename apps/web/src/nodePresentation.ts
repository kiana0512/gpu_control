import type { CloudInstance, CloudServerOverview, NodeInfo } from "./types";

const AUTODL_ACCESS_HOST_SUFFIXES = [
  ".autodl.com",
  ".autodl.art",
  ".seetacloud.com",
] as const;
const COMFYUI_BROWSER_FRAGMENT_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function safeAutoDlAccessUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLocaleLowerCase();
    const officialHost = AUTODL_ACCESS_HOST_SUFFIXES.some(
      (suffix) => hostname === suffix.slice(1) || hostname.endsWith(suffix),
    );
    if (
      url.protocol !== "https:" ||
      !officialHost ||
      url.username ||
      url.password
    )
      return null;
    return url.toString();
  } catch {
    return null;
  }
}

function localComfyUiUrl(
  node: Pick<NodeInfo, "id" | "base_url">,
  browserHostname: string,
): string | null {
  if (!node.base_url) return null;
  try {
    const url = new URL(node.base_url);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password
    )
      return null;
    if (node.id === "control-4090") {
      url.hostname = browserHostname;
      url.hash = "551d82b0-b1fb-483a-a5ea-564bdb813625";
    }
    return url.toString();
  } catch {
    return null;
  }
}

function cloudComfyUiUrl(
  value: string | null,
  labels: Record<string, unknown> | null | undefined,
): string | null {
  const safe = safeAutoDlAccessUrl(value);
  if (!safe) return null;
  const fragment = labels?.comfyui_browser_fragment;
  if (
    typeof fragment !== "string" ||
    !COMFYUI_BROWSER_FRAGMENT_PATTERN.test(fragment)
  )
    return safe;
  const url = new URL(safe);
  // A provider-supplied fragment is authoritative. The durable node label is
  // only the control-plane fallback for AutoDL snapshots that expose a host
  // but omit the ComfyUI browser workspace identifier.
  if (!url.hash) url.hash = fragment;
  return url.toString();
}

export function controlPlaneComfyUiUrl(
  browserHostname: string,
  portValue: unknown,
  fragmentValue?: unknown,
): string | null {
  const port =
    typeof portValue === "number"
      ? portValue
      : typeof portValue === "string" && /^\d+$/.test(portValue)
        ? Number(portValue)
        : Number.NaN;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) return null;
  try {
    const url = new URL(`https://${browserHostname}`);
    url.port = String(port);
    url.pathname = "/";
    url.search = "";
    if (
      typeof fragmentValue === "string" &&
      COMFYUI_BROWSER_FRAGMENT_PATTERN.test(fragmentValue)
    )
      url.hash = fragmentValue;
    return url.toString();
  } catch {
    return null;
  }
}

export function resolveCloudInstanceComfyUiAccessUrl(
  instance: Pick<CloudInstance, "access" | "management">,
  browserHostname: string,
): string | null {
  const providerUrl = cloudComfyUiUrl(instance.access.service_6006, null);
  if (instance.management?.node_id !== "autodl-5090-01") return providerUrl;
  const fragment = providerUrl
    ? new URL(providerUrl).hash.replace(/^#/, "")
    : undefined;
  return (
    controlPlaneComfyUiUrl(browserHostname, 16006, fragment) ?? providerUrl
  );
}

export function resolveComfyUiAccessUrl(
  node: Pick<NodeInfo, "id" | "base_url" | "labels">,
  cloudInstances: readonly CloudInstance[],
  browserHostname: string,
  knownCloudNodeIds: ReadonlySet<string> = new Set(),
): string | null {
  const bindings = cloudInstances.filter(
    (instance) => instance.management?.node_id === node.id,
  );
  if (!bindings.length) {
    if (isKnownAutoDlNode(node, knownCloudNodeIds)) return null;
    return localComfyUiUrl(node, browserHostname);
  }
  if (bindings.length !== 1) return null;
  const direct = controlPlaneComfyUiUrl(
    browserHostname,
    node.labels?.comfyui_browser_proxy_port,
    node.labels?.comfyui_browser_fragment,
  );
  if (direct) return direct;
  return cloudComfyUiUrl(bindings[0].access.service_6006, node.labels);
}

export function isKnownAutoDlNode(
  node: Pick<NodeInfo, "id" | "labels">,
  knownCloudNodeIds: ReadonlySet<string>,
) {
  return node.labels?.provider === "autodl" || knownCloudNodeIds.has(node.id);
}

export function nextCloudComfyUiInventory(
  current: readonly CloudInstance[],
  overview: Pick<
    CloudServerOverview,
    "configured" | "partial_errors" | "instances"
  >,
): CloudInstance[] {
  if (
    !overview.configured ||
    overview.partial_errors?.length ||
    !overview.instances.length
  )
    return [...current];
  return [...overview.instances];
}

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
