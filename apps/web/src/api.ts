import type {
  AuditLog,
  BatchItemsPage,
  Dashboard,
  JobInfo,
  NodeInfo,
  AssetProcessingOverview,
  CloudServerOverview,
  CloudOperationInfo,
  CloudScheduleResult,
  CloudSshCredentials,
  CloudStateResult,
} from "./types";

const TOKEN_KEY = "gpu-control-session";
const REFRESH_TOKEN_KEY = "gpu-control-refresh";
const TOKEN_EXPIRES_KEY = "gpu-control-session-expires";
const ROLE_KEY = "gpu-control-session-role";
const REQUEST_TIMEOUT_MS = 20_000;

function roleFromAccessToken(token: string | null) {
  if (!token) return null;
  try {
    const encoded = token.split(".")[1];
    if (!encoded) return null;
    const normalized = encoded.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const payload = JSON.parse(atob(padded)) as { role?: unknown };
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

export interface AdminSession {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  role: string;
}
export const session = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  refresh: () => sessionStorage.getItem(REFRESH_TOKEN_KEY),
  role: () =>
    sessionStorage.getItem(ROLE_KEY) ??
    roleFromAccessToken(sessionStorage.getItem(TOKEN_KEY)),
  needsRefresh: () =>
    Number(sessionStorage.getItem(TOKEN_EXPIRES_KEY) ?? 0) <=
    Date.now() + 60_000,
  set: (value: AdminSession) => {
    sessionStorage.setItem(TOKEN_KEY, value.access_token);
    sessionStorage.setItem(REFRESH_TOKEN_KEY, value.refresh_token);
    sessionStorage.setItem(
      TOKEN_EXPIRES_KEY,
      String(Date.now() + value.expires_in * 1000),
    );
    sessionStorage.setItem(ROLE_KEY, value.role);
  },
  clear: () => {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_EXPIRES_KEY);
    sessionStorage.removeItem(ROLE_KEY);
  },
};

let refreshPromise: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  const refreshToken = session.refresh();
  if (!refreshToken) return false;
  if (!refreshPromise) {
    refreshPromise = fetch("/admin/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async (response) => {
        if (!response.ok) return false;
        session.set((await response.json()) as AdminSession);
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  retryAfterRefresh = true,
): Promise<T> {
  if (
    session.get() &&
    session.needsRefresh() &&
    !path.startsWith("/admin/auth/")
  )
    await refreshSession();
  const token = session.get();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      signal: options.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "TimeoutError")
      throw new Error("请求超时，请稍后重试");
    throw cause;
  }
  if (response.status === 401 && token && retryAfterRefresh) {
    if (await refreshSession()) return request<T>(path, options, false);
    session.clear();
    window.location.assign("/login?expired=1");
  }
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: { message?: string; code?: string } | string;
    };
    const detail = payload.detail;
    const message =
      typeof detail === "string" ? detail : (detail?.message ?? detail?.code);
    throw new Error(message ?? `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

async function download(path: string): Promise<Blob> {
  const token = session.get();
  const response = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error(`下载失败 (${response.status})`);
  return response.blob();
}

export const api = {
  login: (username: string, password: string) =>
    request<AdminSession>("/admin/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  dashboard: (clientKind: "production" | "test" | "all" = "production") =>
    request<Dashboard>(
      `/admin/dashboard?client_kind=${encodeURIComponent(clientKind)}`,
    ),
  jobs: (
    status?: string,
    clientKind: "production" | "test" | "all" = "production",
    limit = 500,
    includePerformance = false,
  ) => {
    const query = new URLSearchParams({ client_kind: clientKind });
    query.set("limit", String(limit));
    query.set("detail", "summary");
    if (includePerformance) query.set("include_performance", "true");
    if (status) query.set("status", status);
    return request<JobInfo[]>(`/admin/jobs?${query.toString()}`);
  },
  batch: (id: string) =>
    request<JobInfo>(`/admin/batches/${encodeURIComponent(id)}`),
  batchItems: (id: string, offset = 0, limit = 100) =>
    request<BatchItemsPage>(
      `/admin/batches/${encodeURIComponent(id)}/items?offset=${offset}&limit=${limit}`,
    ),
  nodes: () => request<NodeInfo[]>("/admin/nodes"),
  cloudServers: (force = false) =>
    request<CloudServerOverview>(
      `/admin/providers/autodl?force=${force ? "true" : "false"}`,
    ),
  cloudOperation: (id: string) =>
    request<Omit<CloudOperationInfo, "id">>(
      `/admin/providers/autodl/operations/${encodeURIComponent(id)}`,
    ).then((operation) => ({ ...operation, id: operation.operation_id })),
  updateCloudSchedule: (
    product: "app" | "pro",
    id: string,
    scheduledStartAt: string | null,
    scheduledStopAt: string | null,
  ) =>
    request<CloudScheduleResult>(
      `/admin/providers/autodl/instances/${product}/${encodeURIComponent(id)}/schedule`,
      {
        method: "PUT",
        body: JSON.stringify({
          scheduled_start_at: scheduledStartAt,
          scheduled_stop_at: scheduledStopAt,
          reason:
            scheduledStartAt || scheduledStopAt
              ? "管理员从云服务器控制台更新定时启停计划"
              : "管理员从云服务器控制台清除定时启停计划",
          confirm: true,
        }),
      },
    ),
  startCloudInstance: (product: "app" | "pro", id: string) =>
    request<CloudStateResult>(
      `/admin/providers/autodl/instances/${product}/${encodeURIComponent(id)}/start`,
      {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          reason: "管理员从云服务器控制台启动实例",
          confirm: true,
        }),
      },
    ),
  stopCloudInstance: (product: "app" | "pro", id: string) =>
    request<CloudStateResult>(
      `/admin/providers/autodl/instances/${product}/${encodeURIComponent(id)}/stop`,
      {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          reason: "管理员从云服务器控制台关闭实例",
          confirm: true,
        }),
      },
    ),
  cloudSshCredentials: (product: "app" | "pro", id: string) =>
    request<CloudSshCredentials>(
      `/admin/providers/autodl/instances/${product}/${encodeURIComponent(id)}/ssh-credentials`,
      {
        method: "POST",
        body: JSON.stringify({
          reason: "管理员从云服务器控制台请求临时 SSH 凭据",
          confirm: true,
        }),
      },
    ),
  assetProcessing: (limit = 500) =>
    request<AssetProcessingOverview>(
      `/admin/asset-processing?limit=${encodeURIComponent(limit)}`,
    ),
  realesrgan: () =>
    request<{
      status: string;
      model: string;
      model_sha256: string;
      image_version: string;
      ready_nodes: number;
      total_nodes: number;
      queue_depth: number;
      max_queue: number;
      capacity: number;
      nodes: Array<{
        id: string;
        name: string;
        ready: boolean;
        active: number;
        capacity: number;
        device: string | null;
        pytorch: string | null;
        cuda_runtime: string | null;
        python: string | null;
        precision: string | null;
        tile: number | null;
        tile_pad: number | null;
        vram_free_mb: number | null;
        vram_total_mb: number | null;
        last_metrics: Record<string, number>;
        last_error: string | null;
      }>;
      tasks: Array<{
        request_id: string;
        status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
        created_at: string;
        finished_at: string | null;
        node: string | null;
        strength: number;
        width: number;
        height: number;
        input_sha256: string;
        output_sha256: string | null;
        queue_ms: number | null;
        processing_ms: number | null;
        cache: "MISS" | "HIT" | "COALESCED";
        error_code: string | null;
      }>;
      api: {
        enhance: string;
        ready: string;
        capacity: string;
        content_type: string;
        authentication: string;
        idempotency: string;
      };
    }>("/admin/realesrgan"),
  cancelAssetJob: (id: string) =>
    request<{ job_id: string; status: string; cancel_requested: boolean }>(
      `/admin/asset-jobs/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
        body: JSON.stringify({
          reason: "管理员从统一资产处理界面取消任务",
          confirm: true,
        }),
      },
    ),
  retryAssetJob: (id: string) =>
    request<{
      job_id: string;
      status: string;
      stage: string;
      attempt_count: number;
    }>(`/admin/asset-jobs/${encodeURIComponent(id)}/retry`, {
      method: "POST",
      body: JSON.stringify({
        reason: "管理员确认 3090-B 宿主恢复后重试烘焙任务",
        confirm: true,
      }),
    }),
  assetArtifact: (jobId: string, artifactId: string) =>
    download(
      `/admin/asset-jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}`,
    ),
  audits: () => request<AuditLog[]>("/admin/audit-logs"),
  workflows: () => request<Record<string, unknown>[]>("/admin/workflows"),
  importWorkflow: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>("/admin/workflows", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  clients: () => request<Record<string, unknown>[]>("/admin/clients"),
  alerts: () => request<Record<string, unknown>[]>("/admin/alerts"),
  settings: () => request<Record<string, unknown>>("/admin/settings"),
  enableWorkflow: (id: number, enabled: boolean) =>
    request<Record<string, unknown>>(
      `/admin/workflows/${id}/enabled?enabled=${enabled}`,
      {
        method: "PUT",
        body: JSON.stringify({ reason: "管理员控制台操作", confirm: true }),
      },
    ),
  createClient: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>("/admin/clients", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateClient: (id: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(
      `/admin/clients/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    ),
  createKey: (id: string) =>
    request<{ api_key: string; warning: string }>(
      `/admin/clients/${encodeURIComponent(id)}/keys`,
      {
        method: "POST",
        body: JSON.stringify({
          reason: "管理员从控制台创建业务 API Key",
          confirm: true,
        }),
      },
    ),
  updateSetting: (key: string, value: number | boolean | string) =>
    request<Record<string, unknown>>(
      `/admin/settings/${encodeURIComponent(key)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          value,
          reason: "管理员控制台操作",
          confirm: true,
        }),
      },
    ),
  testFeishu: () =>
    request<Record<string, unknown>>("/admin/alerts/test-feishu", {
      method: "POST",
    }),
  logLink: (query: string) =>
    request<{ url: string }>(`/admin/log-link?${query}`),
  setMode: (id: string, mode: NodeInfo["mode"], reason: string) =>
    request<{ id: string; mode: string }>(`/admin/nodes/${id}/mode`, {
      method: "PUT",
      body: JSON.stringify({ mode, reason, confirm: true }),
    }),
  free: (id: string) =>
    request<Record<string, unknown>>(`/admin/nodes/${id}/free`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台释放模型", confirm: true }),
    }),
  interrupt: (id: string) =>
    request<Record<string, unknown>>(`/admin/nodes/${id}/interrupt`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台中断任务", confirm: true }),
    }),
  restart: (id: string) =>
    request<Record<string, unknown>>(`/admin/nodes/${id}/restart`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台安全重启", confirm: true }),
    }),
  start: (id: string) =>
    request<Record<string, unknown>>(`/admin/nodes/${id}/start`, {
      method: "POST",
      body: JSON.stringify({
        reason: "管理员从控制台启动 ComfyUI",
        confirm: true,
      }),
    }),
  stop: (id: string) =>
    request<Record<string, unknown>>(`/admin/nodes/${id}/stop`, {
      method: "POST",
      body: JSON.stringify({
        reason: "管理员从控制台停止 ComfyUI",
        confirm: true,
      }),
    }),
  retry: (id: string) =>
    request<JobInfo>(`/admin/jobs/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台重试", confirm: true }),
    }),
  cancel: (id: string) =>
    request<JobInfo>(`/admin/jobs/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台取消任务", confirm: true }),
    }),
  cancelBatch: (id: string) =>
    request<JobInfo>(`/admin/batches/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason: "管理员从控制台取消批次", confirm: true }),
    }),
  diagnostics: (id: string) =>
    download(`/admin/jobs/${encodeURIComponent(id)}/diagnostics`),
};
