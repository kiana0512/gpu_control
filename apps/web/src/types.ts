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
  codex_cli?: {
    health: "HEALTHY" | "CHECKING" | "DEGRADED" | "UNAVAILABLE";
    host_entry_installed: boolean;
    host_version: string | null;
    runtime_version: string | null;
    auth_status: string;
    probe_status: string;
    probe_latency_ms: number | null;
    last_checked_at: string | null;
    last_success_at: string | null;
    error_code: string | null;
    task: {
      job_id: string;
      external_asset_id: string;
      status: string;
      stage: string;
      input: {
        filename: string;
        sha256: string;
        high_object: string | null;
        reference_object: string | null;
        low_object: string | null;
        reference_view_count: number;
        user_request: string | null;
      };
      output_contract: string[];
      is_active: boolean;
    } | null;
    scheduler_eligible: false;
  };
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
  tenant_id?: string;
  client_kind?: "production" | "test";
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
  client_kind: "production" | "test" | "all";
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

export interface AssetArtifactInfo {
  id: string;
  kind: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  content_type: string;
  download_url: string;
}

export interface AssetWorkerInfo {
  id: string;
  display_name: string;
  node_id: string;
  hostname: string;
  status: "ONLINE" | "OFFLINE";
  reported_status: string;
  blender_version: string;
  skill_version: string;
  cpu_count: number;
  current_jobs: number;
  max_concurrency: number;
  last_heartbeat_at: string | null;
  codex_cli_version: string | null;
  codex_auth_status: string;
  codex_probe_status: string;
  codex_probe_latency_ms: number | null;
  codex_last_checked_at: string | null;
  codex_last_success_at: string | null;
  codex_error_code: string | null;
  retopoflow_version: string | null;
  retopoflow_revision: string | null;
  retopoflow_probe_status: string;
  retopoflow_probe_latency_ms: number | null;
  retopoflow_last_checked_at: string | null;
  retopoflow_error_code: string | null;
}

export interface AssetJobInfo {
  job_id: string;
  external_asset_id: string;
  client_id: string;
  job_type:
    | "UV_UNWRAP"
    | "UV_PROCESS_V2"
    | "RETOPOLOGY_AUDIT"
    | "RETOPOLOGY_PROCESS_V1"
    | string;
  status: string;
  source_filename: string;
  input_sha256: string;
  options: Record<string, unknown>;
  worker_id: string | null;
  progress: number;
  stage: string;
  stage_message: string;
  timing: {
    elapsed_seconds: number;
    estimated_remaining_seconds: number | null;
    last_progress_at: string | null;
  };
  attempt_count: number;
  error: { code: string; message: string | null } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  delivery_ready: boolean;
  review_required: false;
  artifacts_role: "delivery" | "diagnostic" | "retained";
  artifacts: AssetArtifactInfo[];
}

export interface AssetProcessingOverview {
  schema_version: "asset-admin.v4";
  as_of: string;
  summary: {
    counts: Record<string, number>;
    online_workers: number;
    total_slots: number;
    used_slots: number;
    qa_failed: number;
  };
  workers: AssetWorkerInfo[];
  jobs: AssetJobInfo[];
  contracts: {
    uv: {
      submit: string;
      formats: string[];
      artifact_count: number;
      status: string;
      events: string;
    };
    retopology_audit: {
      submit: string;
      format: string;
      success_status: string;
    };
    retopology_process: {
      submit: string;
      format: string;
      success_status: string;
      delivery_policy: string;
      views: string[];
      roles: string[];
      reference_views_optional: boolean;
      maximum_reference_views: number;
      status: string;
      events: string;
    };
    roughness: {
      submit: string;
      format: string;
      runtime: string;
      response: string;
    };
    substance_bake: {
      submit: string;
      format: string;
      profile: string;
      runtime: string;
      artifact_count: number;
      status: string;
      events: string;
    };
  };
}
