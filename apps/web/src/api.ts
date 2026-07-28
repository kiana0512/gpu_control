import type {
  AuditLog,
  BatchItemsPage,
  Dashboard,
  JobInfo,
  NodeInfo,
  AssetProcessingOverview,
} from "./types";

const TOKEN_KEY = "gpu-control-session";
const REFRESH_TOKEN_KEY = "gpu-control-refresh";
const TOKEN_EXPIRES_KEY = "gpu-control-session-expires";
export interface AdminSession {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  role: string;
}
export const session = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  refresh: () => sessionStorage.getItem(REFRESH_TOKEN_KEY),
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
  },
  clear: () => {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    sessionStorage.removeItem(TOKEN_EXPIRES_KEY);
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
  const response = await fetch(path, { ...options, headers });
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
  ) => {
    const query = new URLSearchParams({ client_kind: clientKind });
    query.set("limit", String(limit));
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
  assetProcessing: (limit = 500) =>
    request<AssetProcessingOverview>(
      `/admin/asset-processing?limit=${encodeURIComponent(limit)}`,
    ),
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
