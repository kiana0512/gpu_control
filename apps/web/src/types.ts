export interface NodeInfo {
  id: string;
  display_name: string;
  base_url: string;
  pool: "PRIMARY" | "OVERFLOW";
  mode: "DISABLED" | "RESERVED" | "OVERFLOW" | "ACTIVE" | "DRAINING";
  health: "ONLINE" | "OFFLINE" | "DEGRADED";
  current_jobs: number;
  max_concurrency: number;
  gpu_util_percent: number;
  free_vram_mb: number;
  total_vram_mb: number;
  manual_reserved: boolean;
  foreign_queue_detected: boolean;
  last_heartbeat_at?: string | null;
}
export interface JobInfo {
  job_id: string;
  status: string;
  workflow_key: string;
  workflow_version: string;
  priority: string;
  node_id: string | null;
  prompt_id: string | null;
  progress: number;
  attempt: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: { code: string; message: string } | null;
}
export interface Dashboard {
  jobs: Record<string, number>;
  nodes: NodeInfo[];
  oldest_wait_seconds: number;
  estimated_clear_seconds: number | null;
  submission_trend: { label: string; value: number }[];
  active_alerts: {
    id: string;
    severity: string;
    name: string;
    summary: string;
  }[];
}
export interface AuditLog {
  id: number;
  actor_id: string;
  action: string;
  target_type: string;
  target_id: string;
  result: string;
  created_at: string;
}
