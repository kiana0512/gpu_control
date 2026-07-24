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
  kind?: "job" | "batch";
  job_id: string;
  batch_id?: string;
  external_batch_id?: string;
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
  counts?: BatchCounts;
  node_distribution?: Record<string, number>;
  artifacts?: BatchArtifact[];
}
export interface BatchCounts {
  total: number;
  pending: number;
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
}
export interface BatchArtifact {
  id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
}
export interface BatchItem {
  ordinal: number;
  input_relative_path: string;
  output_relative_path: string;
  status: string;
  job_id: string | null;
  node_id: string | null;
  attempts: number;
  input_sha256: string;
  output_sha256: string | null;
  error: { code: string; message: string } | null;
}
export interface BatchItemsPage {
  batch_id: string;
  total: number;
  offset: number;
  limit: number;
  items: BatchItem[];
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
