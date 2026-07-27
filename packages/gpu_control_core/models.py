from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .enums import (
    BatchItemStatus,
    BatchStatus,
    JobStatus,
    NodeHealth,
    NodeMode,
    NodePool,
    Priority,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_queue", "status", "priority", "pinned", "created_at"),
        Index("ix_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_jobs_node_status", "node_id", "status"),
        Index("ix_jobs_batch_status", "batch_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    workflow_key: Mapped[str] = mapped_column(String(128))
    workflow_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.RECEIVED.value)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL.value)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    job_dir: Mapped[str] = mapped_column(Text)
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_batches.id", ondelete="CASCADE"), nullable=True
    )
    batch_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("job_batch_items.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"), nullable=True)
    prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    events: Mapped[list["JobEvent"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["JobAttempt"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["JobArtifact"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobBatch(Base):
    __tablename__ = "job_batches"
    __table_args__ = (
        Index("ix_job_batches_tenant_status", "tenant_id", "status"),
        Index("ix_job_batches_status_materialized", "status", "last_materialized_at"),
        UniqueConstraint("tenant_id", "external_batch_id", name="uq_batch_external_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), index=True
    )
    external_batch_id: Mapped[str] = mapped_column(String(128))
    workflow_key: Mapped[str] = mapped_column(String(128))
    workflow_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default=BatchStatus.VALIDATING.value)
    failure_policy: Mapped[str] = mapped_column(String(32), default="all_or_nothing")
    output_naming: Mapped[str] = mapped_column(String(32), default="preserve_stem_png")
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    batch_dir: Mapped[str] = mapped_column(Text)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    archive_sha256: Mapped[str] = mapped_column(String(64))
    archive_size_bytes: Mapped[int] = mapped_column(BigInteger)
    total_items: Mapped[int] = mapped_column(Integer)
    pending_items: Mapped[int] = mapped_column(Integer, default=0)
    queued_items: Mapped[int] = mapped_column(Integer, default=0)
    running_items: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_items: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_materialized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobBatchItem(Base):
    __tablename__ = "job_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal", name="uq_batch_item_ordinal"),
        UniqueConstraint("batch_id", "input_relative_path", name="uq_batch_item_input_path"),
        UniqueConstraint("batch_id", "output_relative_path", name="uq_batch_item_output_path"),
        Index("ix_batch_items_batch_status", "batch_id", "status", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("job_batches.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    input_relative_path: Mapped[str] = mapped_column(Text)
    output_relative_path: Mapped[str] = mapped_column(Text)
    input_size_bytes: Mapped[int] = mapped_column(BigInteger)
    input_sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    image_format: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default=BatchItemStatus.PENDING.value)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BatchArtifact(Base):
    __tablename__ = "batch_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("job_batches.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    relative_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchEvent(Base):
    __tablename__ = "batch_events"
    __table_args__ = (UniqueConstraint("batch_id", "sequence", name="uq_batch_event_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("job_batches.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    event: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BatchIdempotencyKey(Base):
    __tablename__ = "batch_idempotency_keys"
    __table_args__ = (
        UniqueConstraint("client_id", "key", name="uq_client_batch_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("job_batches.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobEvent(Base):
    __tablename__ = "job_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    event: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[Job] = relationship(back_populates="events")
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_job_event_sequence"),)


class JobAttempt(Base):
    __tablename__ = "job_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"))
    lease_token: Mapped[str] = mapped_column(String(64), unique=True)
    prompt_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=JobStatus.CLAIMED.value)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    job: Mapped[Job] = relationship(back_populates="attempts")
    __table_args__ = (UniqueConstraint("job_id", "attempt", name="uq_job_attempt"),)


class JobArtifact(Base):
    __tablename__ = "job_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    relative_path: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    download_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    job: Mapped[Job] = relationship(back_populates="artifacts")


class JobCallback(Base):
    __tablename__ = "job_callbacks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    signing_secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallbackAttempt(Base):
    __tablename__ = "callback_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    callback_id: Mapped[str] = mapped_column(
        ForeignKey("job_callbacks.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(256), unique=True)
    agent_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pool: Mapped[str] = mapped_column(String(16), default=NodePool.PRIMARY.value)
    mode: Mapped[str] = mapped_column(String(16), default=NodeMode.ACTIVE.value)
    health: Mapped[str] = mapped_column(String(16), default=NodeHealth.OFFLINE.value)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    current_jobs: Mapped[int] = mapped_column(Integer, default=0)
    manual_reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    external_busy: Mapped[bool] = mapped_column(Boolean, default=False)
    foreign_queue_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    gpu_util_percent: Mapped[float] = mapped_column(Float, default=0)
    free_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    total_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    model_manifest_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    custom_nodes_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NodeLease(Base):
    __tablename__ = "node_leases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), unique=True)
    token: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Workflow(Base):
    __tablename__ = "workflows"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_key: Mapped[str] = mapped_column(
        ForeignKey("workflows.key", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64))
    template: Mapped[dict[str, Any]] = mapped_column(JSON)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(JSON)
    bindings: Mapped[dict[str, Any]] = mapped_column(JSON)
    allowed_class_types: Mapped[list[Any]] = mapped_column(JSON)
    required_models: Mapped[list[Any]] = mapped_column(JSON, default=list)
    required_custom_nodes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    min_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=900)
    node_labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    output_nodes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    template_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("workflow_key", "version", name="uq_workflow_version"),)


class WorkflowNodeCompatibility(Base):
    __tablename__ = "workflow_node_compatibility"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_version_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_versions.id", ondelete="CASCADE")
    )
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))
    compatible: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "node_id", name="uq_workflow_node_compat"),
    )


class ApiClient(Base):
    __tablename__ = "api_clients"
    __table_args__ = (Index("ix_api_clients_role_kind", "role", "client_kind"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), default="client")
    client_kind: Mapped[str] = mapped_column(String(16), default="production")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_queued: Mapped[int] = mapped_column(Integer, default=20)
    max_running: Mapped[int] = mapped_column(Integer, default=1)
    daily_quota: Mapped[int] = mapped_column(Integer, default=1000)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    allowed_ips: Mapped[list[Any]] = mapped_column(JSON, default=list)
    callback_hosts: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_seen_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), index=True
    )
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimitPolicy(Base):
    __tablename__ = "rate_limit_policies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("api_clients.id", ondelete="CASCADE"), unique=True
    )
    requests_per_second: Mapped[float] = mapped_column(Float, default=5)
    burst: Mapped[int] = mapped_column(Integer, default=10)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("api_clients.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("client_id", "key", name="uq_client_idempotency"),)


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_ip: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(24))
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_notified_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    notification_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_notification_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    notification_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
