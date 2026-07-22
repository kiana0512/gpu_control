import type { AuditLog, Dashboard, JobInfo, NodeInfo } from "./types";

const TOKEN_KEY = "gpu-control-session";
export const session = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (value: string) => sessionStorage.setItem(TOKEN_KEY, value),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = session.get();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: { message?: string };
    };
    throw new Error(payload.detail?.message ?? `请求失败 (${response.status})`);
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
    request<{ access_token: string; role: string }>("/admin/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  dashboard: () => request<Dashboard>("/admin/dashboard"),
  jobs: (status?: string) =>
    request<JobInfo[]>(
      `/admin/jobs${status ? `?status=${encodeURIComponent(status)}` : ""}`,
    ),
  nodes: () => request<NodeInfo[]>("/admin/nodes"),
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
  diagnostics: (id: string) =>
    download(`/admin/jobs/${encodeURIComponent(id)}/diagnostics`),
};
