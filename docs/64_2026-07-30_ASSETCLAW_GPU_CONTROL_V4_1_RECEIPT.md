# 动画管家 ↔ GPU Control V4.1 首轮事实回执

> 状态：`IMPLEMENTING / JOINT ACCEPTANCE PENDING / RUNTIME UNCHANGED`
> 核验时间：`2026-07-30T06:21:51Z`
> 核验方式：源码、数据库 schema、当前控制机镜像/容器、只读数据库快照和已有发布文档交叉核对。
> 协作定位：动画管家与 GPU Control 是同一产品体系内的两个系统；本文件用于内部联合整改和端到端
> 验收，目标是同时改善速度、稳定性、恢复能力和问题可定位性。
> 本次没有修改或重启生产服务，没有创建、取消或改派任务，没有修改 ImageClip/ModelViewCreator
> 的仓库、工作流、模型、参数、提示词、图拓扑或输出语义。
>
> **历史快照说明：** 本文件冻结首轮核验时点的生产事实和缺口，不随候选工作树追溯改写。首轮
> 快照中的生产版本为 `1.5.4`、数据库修订为 `20260729_0010`；后续候选修复、冻结的 `691770c`
> 身份、取消 POST 映射和 `20260730_0011` 候选迁移统一见 65 号第二轮回执。任何断电恢复后的实时
> 健康状态都必须重新只读核验，不能从本历史快照推断。

## 1. 回执结论

GPU Control 已完成 V4 的一部分正确性基础，但当前不能回复“V4.1 已对齐”，也不能进入联合速度
验收。准确状态如下：

- 已有源码和发布证据：manifest 1.0、父批次幂等、create 权威接单、服务端持久排队、capacity
  advisory、上传 `overwrite=true`、上传后回读 size/SHA、验证后才提交 prompt、单帧失败继续收敛、
  全部成功才生成父级结果 ZIP、工作流节点领取 fail closed、PostgreSQL 持久状态和基本重启恢复。
- 尚未满足 V4.1：完整父阶段时间、正确的 `started_at` 语义、`performance.nodes[]`、上传/job/prompt
  三层 attempt、稳定 error domain、完整公共取消审计、取消 operation 幂等、节点级 GPU service/P50/P95、
  Scheduler restart/reassignment 计数、queue estimate、straggler、正式故障注入报告和 B97 A/B 报告。
- 发现两个必须先修的服务端 P0：batch child 可通过通用 job cancel 绕过父取消合同；父批次达到
  `SUCCEEDED` 前（包括 `RUNNING/ASSEMBLING/FAILED`），已成功 child 的 artifact 仍可能通过通用
  child artifact API 访问。
- 动画管家合同批准的 `721f7d6` 身份与当前生产事实不一致。当前三节点及已启用 manifest 为
  `2026.07.30-691770c-r1 / 691770cd...`；两者 pipeline SHA 相同。必须由双方书面确认新批准身份，
  不能为匹配旧文档擅自回滚或改写外部 ImageClip。
- 当前 Docker 发布标签/记录是 `1.5.4`，但运行中的 API、Scheduler、Asset API Python 包元数据仍为
  `1.5.1`，Web 构建显示 `1.5.0`；控制面镜像也没有嵌入 GPU Control source revision。关键运行源码
  文件与本轮审阅 HEAD 一致，但版本元数据与镜像来源闭环仍不满足正式版本证据要求。

因此本回执状态是 `IMPLEMENTING`，所有没有原始报告的测试均保持 `null/UNVERIFIED`。

## 2. 已填写回执

```yaml
gpu_control_v4_1_receipt:
  status: IMPLEMENTING
  assessed_at: "2026-07-30T06:21:51Z"
  evidence_level: STATIC_SOURCE_LOCAL_RUNTIME_AND_EXISTING_RELEASE_RECORDS
  runtime_changed_by_this_review: false
  joint_acceptance_passed: false

  reviewed_documents:
    - path: 01_GPU_CONTROL_V4_1_PERFORMANCE_STABILITY_ALIGNMENT.md
      sha256: 5959f2cbee82c6d4c24ce868b63e45df86f8364bb8d81fccfeeb4b4b7a833a61
    - path: 02_BILATERAL_ACTION_MATRIX.md
      sha256: abef0241cd392f201555dc09e10c5e459e1834583f7c2a530c18a605a6cc43be
    - path: 03_JOINT_ACCEPTANCE_AND_BENCHMARK.md
      sha256: ee2918259d5ef96d93d59d75ae0606a265d0456ee0a6e3036f9240e1c67b40d0
    - path: 04_GPU_CONTROL_RECEIPT_TEMPLATE.md
      sha256: ddce974eea00242e1b0ed08684bff76a3dd46b2ff01f53f4256ae4db62f6ca30
    - path: GPU_CONTROL_MATTING_HANDOFF_V4_ASSETCLAW_ALIGNMENT.md
      sha256: 93f638b40b4b009f9d637e3c4e8000f8faaca20bf36966e465cc696ba768b52a

  source:
    repository: https://github.com/kiana0512/gpu_control.git
    git_commit: 63deec8f57dede18ee64703ccc2b2726032e2f07
    reviewed_git_commit: 63deec8f57dede18ee64703ccc2b2726032e2f07
    release_record_git_commit: 50f1d7b95e038fc5f313843dd9725c12a6b5e099
    deployed_control_plane_git_commit: UNKNOWN
    release_record_commit_time: "2026-07-30T12:51:28+08:00"
    production_release_published_at: UNKNOWN
    release_label: "1.5.4"
    gpu_control_version: "1.5.4"
    gpu_control_version_scope: declared_release_label_not_embedded_runtime_version
    runtime_python_distribution_versions:
      api: "1.5.1"
      scheduler: "1.5.1"
      asset_api: "1.5.1"
    runtime_web_display_version: "1.5.0"
    version_metadata_aligned: false
    critical_runtime_source_files_match_reviewed_head: true
    critical_runtime_source_verification_scope:
      - apps/api/src/gpu_control_api/main.py
      - apps/scheduler/src/gpu_control_scheduler/main.py
      - packages/gpu_control_core/models.py
      - packages/gpu_control_core/batches.py
      - packages/comfy_client/client.py
      - packages/gpu_control_core/repository.py
    image_digest_kind: docker_image_id
    registry_manifest_digests_returned: false
    api_image_digest: sha256:06147d527d4a146141c9cf3c56b62c474096543cbdbde2050b2d1a652e478cb3
    scheduler_image_digest: sha256:f9569a39438bbbc63a9b3f8c6ff3991e1bce67efddc69167467549c16f4a227b
    web_image_digest: sha256:8f9558646a306600a24c2898355901a85b0e3b4fd94c3e807b7d2fa27cf408ae
    asset_api_image_digest: sha256:827053b49248ea22296fb3b78fb3012f1a158577f34921b30dcf140567ce0c3d
    node_worker_image_digests:
      component: ComfyUI
      control-4090: sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
      worker-3090-a: sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
      worker-3090-b: sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea
      evidence:
        control-4090: local_runtime_inspect
        worker-3090-a: existing_release_record_not_live_remote_inspect
        worker-3090-b: existing_release_record_not_live_remote_inspect
    node_agent_image_digest: null
    node_agent_packaging: systemd_host_service_not_a_container_image
    control_plane_image_revision_embedded: false
    asset_worker_image_revision_embedded: false
    comfyui_image_revision_embedded: true
    comfyui_gpu_control_revision: 3e2741aca0cc61410a26c29551a7097d34989aa5
    comfyui_upstream_commit: 700821e1364eaab0e8f21c538a2131719fec57bf
    comfyui_lock_sha256: 5ef4ba8cc88fd24a0fc81c997420bcbbf5cbae96fb96aff1276b7c3c5d60648d
    control_plane_source_image_binding: RELEASE_DOCUMENT_ASSOCIATION_NOT_EMBEDDED_PROVENANCE
    runtime_container_started_at:
      api: "2026-07-30T02:46:12Z"
      scheduler: "2026-07-30T02:46:29Z"
      asset_api: "2026-07-30T02:46:12Z"
      web: "2026-07-29T10:52:48Z"

  runtime_snapshot:
    captured_at: "2026-07-30T06:21:51Z"
    database_revision: "20260729_0010"
    control_services_healthy: true
    nodes_online: 3
    active_parent_batches: 0
    active_gpu_jobs: 0
    snapshot_is_joint_acceptance_evidence: false
    snapshot_is_continuous_availability_guarantee: false

  source_confirmed_findings:
    child_cancel_bypass:
      status: SOURCE_CONFIRMED_RUNTIME_NOT_EXERCISED
      endpoint: apps/api/src/gpu_control_api/main.py::cancel_job
    pre_success_child_artifact_access:
      status: SOURCE_CONFIRMED_RUNTIME_NOT_EXERCISED
      endpoint: apps/api/src/gpu_control_api/main.py::job_artifact_file
    incompatible_node_selection_head_of_line_block:
      status: SOURCE_CONFIRMED_RUNTIME_NOT_EXERCISED
      endpoint: apps/scheduler/src/gpu_control_scheduler/main.py::schedule_available

  api_compatibility:
    manifest_schema_version: "1.0"
    v4_contract_preserved: false
    v4_contract_gap_reason:
      - batch_child_cancel_can_bypass_parent_cancel_contract
      - successful_child_artifact_can_be_accessed_when_parent_failed
    additive_parent_status_only: true
    additive_parent_status_only_is_compatibility_rule: true
    v4_1_additive_parent_fields_deployed: false
    create_batch_is_authoritative_admission: true
    capacity_is_advisory_only: true
    server_persists_queue_after_accept: true

  timing_contract:
    created_at: true
    validated_at: false
    queued_at: false
    started_at_means_first_gpu_execution: false
    last_progress_at: false
    execution_finished_at: false
    assembling_started_at: false
    artifact_ready_at: false
    finished_at: true
    updated_at: false
    timestamps_utc_iso8601: true
    timestamps_monotonic_and_restart_stable: false
    sparse_create_response_safe_merge_contract: false
    queue_wait_formula_available: false
    parent_gpu_wall_formula_available: false
    assembly_formula_available: false
    implementation_location:
      model: packages/gpu_control_core/models.py::JobBatch
      serializer: apps/api/src/gpu_control_api/main.py::batch_payload
      transition: packages/gpu_control_core/batches.py::transition_batch
    note: parent_started_at_is_currently_written_when_parent_enters_RUNNING_and_can_precede_GPU_execution

  performance_contract:
    performance_schema_version_returned: false
    parent_returns_frames_total: false
    parent_returns_input_pixels_total: false
    parent_returns_reassignments: false
    parent_returns_scheduler_restart_count: false
    node_returns_gpu_model_and_worker_version: false
    node_returns_frames_assigned_succeeded_failed: false
    node_returns_frame_attempts: false
    node_returns_upload_integrity_attempts: false
    node_returns_job_and_prompt_attempts_separately: false
    node_returns_upload_and_prompt_attempts_separately: false
    node_returns_gpu_service_ms: false
    node_returns_frame_latency_p50_p95: false
    node_returns_workflow_load_ms: false
    node_returns_first_and_last_execution_at: false
    node_returns_max_concurrent_prompts: false
    node_metrics_reconcile_with_parent_counts: false
    child_performance_endpoint: null
    existing_partial_data:
      parent_node_distribution: true
      child_final_node_and_combined_attempt_count: true
      durable_job_attempt_rows: true
    existing_partial_data_qualifies_for_v4_1_baseline: false

  workflow_identity:
    key: imageclip-rgba
    version: 2026.07.30-691770c-r1
    imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
    pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
    model_hashes: null
    contract_requested:
      key: imageclip-rgba
      version: 2026.07.27-721f7d6-r1
      imageclip_commit: 721f7d68635ee36d45f545ce2c82037046147442
      pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
      output_node: "SaveImage #25"
    current_runtime:
      key: imageclip-rgba
      version: 2026.07.30-691770c-r1
      imageclip_commit: 691770cd6a59fd7c51391456fe900dc57a313233
      pipeline_sha256: 00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b
      output_node: "SaveImage #25"
      enabled_in_production_database: true
      enabled_in_repository_static_manifest: false
      compatible_node_registry_records: 3
      compatible_node_registry_records_do_not_guarantee_current_online_capacity: true
      node_identity_observed_at: "2026-07-30T06:21:51Z"
    contract_identity_matches_runtime: false
    returned_on_create: false
    returned_on_initial_create: false
    returned_on_every_parent_get: false
    key_and_version_returned_on_every_parent_get: true
    pipeline_identity_resolved_from_current_workflow_row: true
    pipeline_identity_snapshotted_on_batch: false
    returned_in_artifact_metadata: false
    returned_in_artifact_manifest: false
    returned_in_download_manifest: false
    node_claim_fail_closed_on_mismatch: true
    node_claim_fail_closed_scope: current_gpu_control_manifest_and_node_label_compatibility
    idempotency_request_hash_includes_workflow_key_or_version_and_manifest: true
    idempotency_request_hash_includes_pipeline_commit_and_sha256: false
    action_required: JOINTLY_CONFIRM_CURRENT_691770C_IDENTITY_OR_AUTHORIZE_AN_EXACT_VERSION_CHANGE

  upload_integrity:
    overwrite_true: true
    readback_size_check: true
    readback_sha256_check: true
    max_integrity_attempts: 3
    prompt_blocked_until_verified: true
    upload_attempt_separate_from_job_attempt: false
    upload_attempt_persisted_in_database: false
    zero_byte_source_rejected: true
    zero_byte_remote_residue_repair_test: tests/unit/test_fake_comfyui.py::test_comfy_client_overwrites_and_repairs_zero_byte_upload
    zero_byte_source_rejection_test: null
    zero_byte_fault_test_id: null
    zero_byte_fault_report_id: null
    zero_byte_fault_test_status: SOURCE_PRESENT_NOT_RERUN_NOT_JOINTLY_ACCEPTED

  failure_and_error_codes:
    stable_error_domain_and_code: false
    upload_failure_distinct_from_prompt_failure: true
    child_failure_keeps_parent_running_until_all_settle: true
    permanent_child_failure_makes_parent_failed: true
    failed_parent_has_no_result_archive: true
    failed_parent_has_no_parent_result_archive: true
    successful_child_artifact_hidden_until_parent_succeeded: false
    failure_never_sets_cancel_requested: true
    failure_never_sets_parent_cancel_requested: true
    prompt_timeout_does_not_cancel_parent: true
    prompt_timeout_interrupts_comfy_execution: true
    prompt_timeout_code_matches_contract: false
    node_offline_lease_reassignment_proven: false
    implementation_location:
      batch_convergence: apps/scheduler/src/gpu_control_scheduler/main.py::sync_batch_state
      prompt_timeout: apps/scheduler/src/gpu_control_scheduler/main.py::timeout_watchdog
      artifact_guard: apps/api/src/gpu_control_api/main.py::batch_artifact_file

  cancellation:
    only_authenticated_user_or_admin_can_cancel: false
    owner_principal_or_admin_route_required: true
    production_api_key_required_for_all_client_cancel_requests: false
    required_idempotency_key: "<external_batch_id>:cancel"
    required_for_nonterminal_parent_cancel: "<external_batch_id>:cancel"
    terminal_replay_key_value_validated: false
    child_cancel_requires_idempotency_key: false
    timeout_never_cancels: true
    timeout_never_cancels_parent: true
    node_failure_never_cancels: true
    node_failure_never_cancels_parent: true
    cancel_audit_has_actor_source_reason_request_id: false
    cancel_audit_has_idempotency_key: false
    cancel_audit_has_requested_at: false
    replay_returns_same_cancel_operation: false
    cancelled_without_audit_is_impossible: false
    batch_child_cancel_endpoint_blocked: false
    public_cancel_has_durable_cancel_operation: false
    admin_cancel_is_audited: true

  recovery:
    postgres_is_source_of_truth: true
    scheduler_restart_reuses_batch_job_attempt_ids: false
    submitted_prompt_with_persisted_prompt_id_is_recovered: true
    node_lease_and_comfy_history_reconciled_before_retry: false
    node_offline_reassigns_without_parent_cancel: false
    batch_and_job_identity_is_durable: true
    timing_and_performance_survive_restart: false
    prompt_submit_prompt_id_commit_is_atomic: false
    note: prompt_submit_success_before_prompt_id_commit_remains_a_duplicate_execution_window

  scheduling_optimization:
    weighted_by_node_pixel_throughput: false
    dynamic_work_stealing: false
    work_conserving_dynamic_pull_for_unassigned_frames: true
    current_dynamic_pull_form: bounded_shared_frame_queue_and_next_free_node_claim
    already_assigned_or_running_work_can_be_stolen: false
    speculative_retry_for_tail_frames: false
    model_or_workflow_kept_warm: true
    warm_behavior: best_effort_warm_affinity
    advisory_estimated_queue_ms_returned: false
    compatible_online_nodes_returned: false
    observed_at_returned: false
    straggler_ratio_measurable: false

  security:
    production_api_key_enabled: true
    production_api_key_enabled_scope: platform_capability_and_existing_enabled_keys
    api_key_capability_enabled: true
    assetclaw_production_api_key_confirmed: false
    tls_verify_required: false
    tls_verify_required_scope: assetclaw_client_verification_not_confirmed
    tls_server_enabled_and_http_redirected: true
    assetclaw_client_tls_verification_confirmed: false
    approved_ca_digest: ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b
    test_and_production_tenants_isolated: false
    production_and_test_client_kinds_isolated: false
    tenant_ownership_access_isolated: true
    test_jobs_deprioritized_behind_eligible_production: true
    execution_resources_physically_isolated: false
    dedicated_v4_1_test_tenant_and_key_provisioned: false
    secrets_written_to_this_document: false

  tests:
    normal_and_idempotency_report_id: null
    zero_byte_upload_report_id: null
    sha_mismatch_report_id: null
    prompt_timeout_report_id: null
    permanent_frame_failure_report_id: null
    node_offline_report_id: null
    scheduler_restart_report_id: null
    invalid_cancel_state_report_id: null
    valid_cancel_replay_report_id: null
    workflow_drift_report_id: null
    artifact_tamper_report_id: null
    create_response_loss_report_id: null
    benchmark_session_id: null
    machine_report_sha256: null
    raw_evidence:
      report_json_path: null
      report_md_path: null
      parent_status_json_path: null
      child_jobs_path: null
      request_ids_path: null
      trace_ids_path: null
      artifact_sha_path: null
    acceptance_matrix:
      "N-01": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: FIXED_B1_AND_ISOLATED_SESSION_MISSING}
      "N-02": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: FIXED_B30_AND_JOINT_RUN_MISSING}
      "N-03": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: FIXED_B64_AND_CAPACITY_SATURATION_RUN_MISSING}
      "N-04": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: CREATE_RESPONSE_LOSS_RAW_REPORT_MISSING}
      "N-05": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: ASSETCLAW_WORKER_RESTART_REPORT_MISSING}
      "N-06": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: SCHEDULER_PROCESS_RESTART_REPORT_MISSING}
      "N-07": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: THREE_CONCURRENT_B97_BUNDLES_MISSING}
      "N-08": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: INTERRUPTED_DOWNLOAD_REPORT_MISSING}
      "F-01": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: REMOTE_ZERO_BYTE_FAULT_RAW_REPORT_MISSING}
      "F-02": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: THREE_INTEGRITY_FAILURES_RAW_REPORT_MISSING}
      "F-03": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: PROMPT_TIMEOUT_RAW_REPORT_MISSING}
      "F-04": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: PERMANENT_FRAME_FAILURE_RAW_REPORT_MISSING}
      "F-05": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: SAFE_NODE_OFFLINE_WINDOW_AND_RAW_REPORT_MISSING}
      "F-06": {status: UNVERIFIED, report_id: null, primary_owner: GPU_CONTROL, blocker: HEARTBEAT_FLAP_AND_RECONCILIATION_REPORT_MISSING}
      "F-07": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: TEN_MINUTE_POLL_OUTAGE_REPORT_MISSING}
      "F-08": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: WATCHDOG_EXPIRY_REPORT_MISSING}
      "F-09": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: ILLEGAL_REMOTE_CANCELLED_REPORT_MISSING}
      "F-10": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: LEGAL_CANCEL_INTENT_AND_AUDIT_NOT_IMPLEMENTED}
      "F-11": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: WORKFLOW_DRIFT_REPORT_AND_IDENTITY_DECISION_MISSING}
      "F-12": {status: UNVERIFIED, report_id: null, primary_owner: JOINT, blocker: ARTIFACT_TAMPER_QUALITY_REPORT_MISSING}
      "F-13": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: CHERRY_WORKER_RESTART_REPORT_MISSING}
      "F-14": {status: UNVERIFIED, report_id: null, primary_owner: ASSETCLAW, blocker: DELIVERY_RECOVERY_AND_ACK_REPORT_MISSING}
    existing_release_test_summary:
      python: "101/101 PASS (release record; raw current report unavailable)"
      backup_restore: "23/23 PASS (release record; raw current report unavailable)"
      web_vitest: "3/3 PASS (release record; raw current report unavailable)"
      web_build: "PASS (release record; raw current report unavailable)"
    rerun_during_this_review: false
    rerun_blocker: pytest_and_required_test_dependencies_are_not_installed_in_available_python_environments
    qualifies_as_v4_1_joint_test_evidence: false

  performance_result:
    fixed_bundle_sha256: null
    fixed_bundles_provided: false
    fixed_bundles:
      B1: {frames: 1, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
      B6: {frames: 6, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
      B30: {frames: 30, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
      B64: {frames: 64, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
      B97: {frames: 97, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
      B300: {frames: 300, manifest_sha256: null, bundle_sha256: null, status: NOT_PROVIDED}
    benchmark_run_counts:
      B1: {local_hot: 0, one_node_hot: 0}
      B6: {local_hot: 0, one_node_hot: 0, two_node_hot: 0, three_node_hot: 0}
      B30: {local_hot: 0, one_node_hot: 0, two_node_hot: 0, three_node_hot: 0}
      B97: {local_hot: 0, one_node_hot: 0, two_node_hot: 0, three_node_hot: 0}
      B300: {local_hot: 0, one_node_hot: 0, two_node_hot: 0, three_node_hot: 0}
      concurrent_3xB97_three_node_groups: 0
    cold_runs_completed: 0
    b97_three_node_runs: 0
    b97_three_node_gpu_p50_seconds: null
    b97_three_node_gpu_p90_seconds: null
    b97_paired_speedup_median: null
    b97_paired_speedup_p10: null
    queue_wait_p90_ms: null
    artifact_return_p95_ms: null
    straggler_ratio_p95: null
    gpu_batch_success_rate_7d: null
    all_performance_gates_passed: false
    status: PENDING_FIXED_BUNDLES_AND_JOINT_BENCHMARK

  rollout:
    v4_1_rollout_started: false
    active_batches_drained: false
    comfy_queues_checked: false
    rollout_started_at: null
    rollout_finished_at: null
    canary_10_percent_passed: false
    canary_50_percent_passed: false
    full_rollout_passed: false
    seven_day_observation_passed: false
    rollback_version: UNKNOWN
    note: current_1_5_4_release_is_not_a_v4_1_acceptance_rollout

  ownership:
    api_owner: UNKNOWN
    scheduler_owner: UNKNOWN
    node_worker_owner: UNKNOWN
    observability_owner: UNKNOWN
    acceptance_owner: UNKNOWN

  unresolved_items:
    - id: P0-VERSION-EVIDENCE
      matrix_ids: [P0-06]
      description: release_label_and_runtime_component_versions_are_not_aligned_and_control_images_lack_source_revision
      owner: GPU_CONTROL_RELEASE_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: rebuild_and_publish_commit_bound_images_then_return_registry_digests_and_release_time
    - id: P0-IDENTITY
      matrix_ids: [P0-06]
      description: contract_721f7d6_identity_does_not_match_current_691770c_runtime_identity
      owner: JOINT_ACCEPTANCE_OWNERS_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: joint_written_decision_plus_batch_identity_snapshot_migration_and_backfill_required
    - id: P0-CANCEL-CHILD
      matrix_ids: [P0-03, P0-04]
      description: generic_child_job_cancel_can_bypass_parent_cancel_contract_and_stall_parent
      owner: GPU_CONTROL_API_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: implementation_and_regression_tests_required
    - id: P0-CANCEL-AUDIT
      matrix_ids: [P0-03, P0-04]
      description: public_parent_cancel_has_no_complete_actor_source_reason_request_id_operation_audit
      owner: GPU_CONTROL_API_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: schema_api_and_idempotent_cancel_operation_required
    - id: P0-CANCEL-AUTH
      matrix_ids: [P0-03, P0-04]
      description: source_ip_auto_enrollment_means_a_dedicated_assetclaw_api_key_is_not_universally_enforced
      owner: GPU_CONTROL_API_AND_SECURITY_OWNERS_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: provision_dedicated_identity_and_freeze_auto_enrollment_policy_before_acceptance
    - id: P0-PARTIAL-ARTIFACT
      matrix_ids: [P0-06]
      description: child_artifact_can_be_accessed_before_parent_all_or_nothing_success
      owner: GPU_CONTROL_API_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: authorization_guard_and_failure_regression_required
    - id: P1-TIMING
      matrix_ids: [P1-03, P1-04]
      description: v4_1_parent_stage_timestamps_and_first_gpu_started_semantics_missing
      owner: GPU_CONTROL_API_AND_SCHEDULER_OWNERS_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: migration_serializer_scheduler_and_monotonic_restart_tests_required
    - id: P1-PERFORMANCE
      matrix_ids: [P2-01, P3-02, P3-03, P3-04, P3-05]
      description: parent_and_node_performance_schema_attempt_layers_and_straggler_metrics_missing
      owner: GPU_CONTROL_SCHEDULER_AND_OBSERVABILITY_OWNERS_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: persistent_measurement_contract_and_api_aggregation_required
    - id: P1-RECOVERY
      matrix_ids: [P1-01, P1-06]
      description: prompt_submit_commit_window_and_missing_gpu_lease_expiry_reconciliation
      owner: GPU_CONTROL_SCHEDULER_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: recovery_protocol_and_process_level_fault_tests_required
    - id: P1-COMPAT-NODE-SELECTION
      matrix_ids: [P2-03, P2-06]
      description: scheduler_can_select_an_incompatible_node_then_break_without_trying_other_compatible_nodes
      owner: GPU_CONTROL_SCHEDULER_OWNER_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: compatibility_aware_node_selection_and_mixed_compatibility_regression_required
    - id: P1-EVIDENCE
      matrix_ids: [P0-07, P1-01, P1-06, P2-02, P2-03, P2-04, P2-05, P2-06]
      description: v4_1_N01_N08_F01_F14_raw_reports_and_B97_benchmark_do_not_exist
      owner: JOINT_ACCEPTANCE_OWNERS_PENDING_ASSIGNMENT
      target_date: UNKNOWN
      blocker: fixed_bundles_isolated_tenant_safe_window_and_both_sides_test_inputs_required

  declaration:
    declaration_scope: NOT_YET_ATTESTED_UNTIL_NAMED_OWNER_SIGNS
    no_fastest_sample_cherry_picking: null
    no_quality_gate_bypass: null
    no_runtime_claim_without_joint_evidence: null
    signed_by: UNSIGNED_OWNER_ASSIGNMENT_PENDING
    signed_at: null
```

其中 owner、ETA、正式发布时间、部署 commit 和回滚版本保持 `UNKNOWN`，是因为现有文档与运行证据
没有给出可核验值；首轮回执不代替负责人虚构姓名或日期。双方指定责任人后，应在下一版签署回执中
替换这些占位值。

## 3. 实现位置与证据索引

| 合同能力 | 当前实现/证据 | 结论 |
|---|---|---|
| manifest 1.0、ZIP/路径/SHA 验证 | `packages/gpu_control_core/batches.py` 的 `BatchManifest`、`extract_batch_archive` | 已实现，待联合素材复验 |
| create、幂等与持久排队 | `apps/api/src/gpu_control_api/main.py` 的 `create_imageclip_batch` | 已实现 |
| capacity advisory | `apps/api/src/gpu_control_api/main.py` 的 `scheduler_capacity` | 已实现；没有 queue estimate |
| 父状态 serializer | `apps/api/src/gpu_control_api/main.py` 的 `batch_payload` | 仅基础时间和 node distribution |
| Scheduler claim/lease | `packages/gpu_control_core/repository.py` 的 `claim_next_job`、`release_lease` | 基础领取已实现；租约到期对账不足 |
| Scheduler restart recovery | `apps/scheduler/src/gpu_control_scheduler/main.py` 的 `reconcile` | 部分实现；仍有 prompt 双写窗口 |
| 上传完整性 | `packages/comfy_client/client.py` 的 `upload`、`remote_digest` | 功能已实现；attempt 未持久化 |
| prompt/history | `apps/scheduler/src/gpu_control_scheduler/main.py` 的 `execute`、`finish_from_history` | 基础实现；缺独立 prompt attempt |
| heartbeat/兼容门禁 | Scheduler `update_node_health` + `repository.claim_next_job` | claim 时再次比对 labels，fail closed |
| 兼容节点选择 | Scheduler `schedule_available` | 先选通用节点再过滤 workflow；可能整轮被不兼容节点阻塞 |
| 父失败收敛 | Scheduler `sync_batch_state` | 单帧失败后其余继续，最终父 FAILED |
| artifact assembly | Scheduler `assemble_batch` + `batches.build_result_archive` | 父级 ZIP all-or-nothing；父成功前 child 下载需补门禁（源码确认，未做生产注入） |
| 取消 | API `cancel_batch`、`admin_cancel_batch`、通用 `cancel_job` | 公共审计、认证和 child 边界未满足合同（源码确认，未做生产注入） |
| 性能聚合 | 目前只有 `node_distribution` 与内部 `JobAttempt` | V4.1 未实现 |
| 当前真实批次 runner | `scripts/run_batch_gpu_acceptance.py` | 支持基础批次和结果校验，不是 V4.1 A/B runner |
| 发布版本/镜像 | `docs/63_...`、`artifacts/control-plane/1.5.4/README.md`、运行容器 metadata | Docker label 为 1.5.4，运行包/UI 为 1.5.1/1.5.0；控制面镜像未嵌 source revision |

## 4. 行动矩阵回填

这里沿用对方文档规定的状态值；`DEPLOYED_NOT_ACCEPTED` 表示运行代码存在，但没有本轮联合原始证据。

| ID | GPU Control 当前状态 | 说明 |
|---|---|---|
| 版本证据门禁 | `BLOCKED` | 发布标签与运行组件版本元数据不一致，控制面镜像缺 source revision |
| P0-02 | `DEPLOYED_NOT_ACCEPTED` | Scheduler timeout/node failure 不设置父 cancel；缺正式故障报告 |
| P0-03 | `NOT_STARTED` | 公共父 cancel 缺完整 operation/audit；child cancel 还能绕过父合同 |
| P0-04 | `NOT_STARTED` | 无 audit 的 `CANCELLED` 尚未做到服务端不可达 |
| P0-06 | `BLOCKED` | create/artifact 身份字段缺失，且合同身份与当前生产身份冲突 |
| P0-07 | `DEPLOYED_NOT_ACCEPTED` | overwrite、回读 size/SHA、prompt 前门禁存在；缺联合 F-01/F-02 报告 |
| P1-01 | `IN_PROGRESS` | child retry/attempt 存在，但 attempt 分层、租约改派和恢复证据不足 |
| P1-03 | `NOT_STARTED` | 真排队时间字段不存在，当前 started 语义不符合合同 |
| P1-04 | `NOT_STARTED` | validated/execution-finished/assembly/artifact-ready 均未持久化 |
| P1-06 | `IN_PROGRESS` | 有父/子事件和基本 reconcile，无 restart/reassignment 权威计数 |
| P2-01 | `NOT_STARTED` | 没有 `performance.nodes[]` |
| P2-02 | `NOT_STARTED` | 没有固定 B97 的节点并发、显存、加载和慢分片联合诊断报告 |
| P2-03 | `IN_PROGRESS` | 有未领取帧的动态 pull；无 pixel throughput 加权、真正 work stealing 和 straggler 指标 |
| P2-04 | `NOT_STARTED` | capacity 没有 `estimated_queue_ms/compatible_online_nodes/observed_at` |
| P2-05 | `IN_PROGRESS` | 有 best-effort workflow warm affinity；固定开销尚未按 B1/B6/B30 证明下降 |
| P2-06 | `IN_PROGRESS` | 未分配帧动态领取存在，无尾帧推测性重算 |
| P3-02 | `NOT_STARTED` | 没有 pixels、纯 `gpu_service_ms` 和加权/Mpixel 吞吐所需字段 |
| P3-03 | `IN_PROGRESS` | key/version 可返回，但完整 pipeline/model/node 身份没有形成不可变性能维度 |
| P3-04 | `NOT_STARTED` | 稳定 error domain/code 和性能异常字段未完成 |
| P3-05 | `NOT_STARTED` | GPU span 尚不能用 trace/request ID 与动画管家 true-E2E 稳定关联 |

## 5. 联合验收前需要动画管家提供/确认

以下内容没有安全地进入 GPU Control 仓库，不能由 GPU Control 猜测：

1. 对 `721f7d6` 与当前 `691770c` 身份冲突作书面联合决定：可以由动画管家修订批准基线，也可以
   明确授权一次精确版本变更；在决定前不运行速度验收，不擅自改动 ImageClip 工作流。
2. 提供不可变 B1/B6/B30/B64/B97/B300 素材、逐包 SHA-256、逐帧 manifest，以及模型摘要。
3. 提供动画管家 source commit、配置 digest、本机 4070 Ti/驱动、同素材本机结果和网络 RTT/带宽。
4. 通过安全渠道交换测试 API 身份；Markdown 中不写密钥。双方共同建立隔离 tenant、session、
   generation 和输出目录，并冻结来源 IP 自动登记策略。
5. 指定 API、Scheduler、Node Worker、可观测性和联合验收负责人，并指定明确回滚版本。
6. 动画管家先完成 watchdog 不自动 cancel、完整 cancel intent、非法 CANCELLED 对账、身份发布硬门禁、
   stage/trace/idle gap/delivery ack 等其责任范围内的 P0/P1 项。

## 6. 联合签署前需要定稿的合同歧义

- `started_at → execution_finished_at` 是父任务 GPU 阶段 wall clock，可能包含执行期空档；不能与每节点
  实际忙碌累计 `gpu_service_ms` 混称“纯 GPU 时间”。
- 改派后 `frames_assigned` 应明确是唯一 ordinal 数还是累计领取次数；建议同时返回
  `unique_frames_assigned` 与 `assignment_attempts`。
- 模板中的 node worker digest 应明确为 ComfyUI、Node Agent 还是 Asset Worker。本回执按 GPU 执行
  ComfyUI 镜像填写，并明确 Node Agent 没有容器 digest。
- B64 出现在固定素材和 N-03，却没有进入 A/B 主矩阵；应决定是否加入 1/2/3 节点速度矩阵。
- B97 每种模式只有 5 次热跑时，“至少 90%”实际等价于 5/5；若需要稳定 P90/P10 和 bootstrap CI，
  应增加样本量或冻结小样本统计方法。
- 跨动画管家、控制机和三节点的阶段时间需要补充 NTP/chrony 状态及最大允许时钟偏差。

## 7. 当前可引用但不能冒充 V4.1 验收的历史证据

- GPU Control 1.5.4 发布记录声称 Python `101/101`、备份/恢复 `23/23`、Web `3/3` 和生产构建通过；
  当前没有这些测试的原始报告，且运行组件版本 metadata 仍显示 1.5.1/1.5.0。
- 历史 6 帧和 30 帧真实批次分别完成过三节点结果校验；这些不是固定 B6/B30 同素材 A/B。
- 当前仓库 runner 可验证父批次幂等、结果 ZIP 集合、ordinal、路径、输入/输出 SHA、PNG 和 Alpha。
- 当前没有完整 V4.1 `report.json/report.md`，也没有 N-01～N-08、F-01～F-14 和 B97 正式矩阵的
  不可变原始证据。因此所有正式 performance 数值保持 `null`。

本文件是第一轮事实回执，不是上线签字。P0/P1 实现、双方固定输入、故障注入、速度/质量门槛、
10% → 50% → 全量灰度和连续 7 天观察全部完成后，才能生成下一版签署回执并把状态改为
`FROZEN / PRODUCTION ACCEPTED`。
