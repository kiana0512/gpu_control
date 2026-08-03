from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://gpu_control:gpu_control@localhost/gpu_control"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-only-change-me"
    api_key_pepper: str = "development-only-change-me"
    node_agent_hmac_secret: str = "development-only-change-me"
    node_agent_hmac_secret_worker_3090_a: str = ""
    node_agent_hmac_secret_worker_3090_b: str = ""
    node_agent_hmac_secret_control_4090: str = ""
    alertmanager_webhook_token: str = "development-only-change-me"
    job_root: Path = Path("storage/jobs")
    model_root: Path = Path("storage/models")
    public_base_url: str = "http://localhost:8000"
    grafana_base_url: str = "http://localhost:3000"
    allowed_callback_hosts: str = ""
    feishu_webhook_url: str = ""
    feishu_signing_secret: str = ""
    scheduler_fallback_scan_ms: int = Field(500, ge=100, le=60_000)
    node_heartbeat_timeout_seconds: int = Field(20, ge=5, le=600)
    node_max_concurrency: int = Field(1, ge=1, le=1)
    default_tenant_max_queued: int = Field(20, ge=1, le=10_000)
    default_tenant_max_running: int = Field(1, ge=1, le=10)
    system_max_queued: int = Field(500, ge=1, le=100_000)
    priority_aging_seconds: int = Field(300, ge=10, le=86_400)
    overflow_queue_threshold: int = Field(20, ge=1, le=100_000)
    overflow_wait_threshold_seconds: int = Field(120, ge=1, le=86_400)
    overflow_4090_max_gpu_util_percent: float = Field(20, ge=0, le=100)
    overflow_4090_min_free_vram_mb: int = Field(20_000, ge=0, le=200_000)
    overflow_4090_auto_enabled: bool = False
    overflow_4090_allowed_windows: str = ""
    overflow_4090_sentinel: Path = Path("/run/gpu-control/4090.reserved")
    job_default_timeout_seconds: int = Field(900, ge=10, le=86_400)
    job_max_attempts: int = Field(3, ge=1, le=10)
    max_upload_bytes: int = Field(52_428_800, ge=1024, le=2_147_483_648)
    max_image_pixels: int = Field(40_000_000, ge=1, le=500_000_000)
    batch_max_frames: int = Field(5_000, ge=1, le=100_000)
    batch_max_archive_bytes: int = Field(107_374_182_400, ge=1024, le=1_099_511_627_776)
    batch_max_uncompressed_bytes: int = Field(
        107_374_182_400, ge=1024, le=1_099_511_627_776
    )
    batch_max_frame_bytes: int = Field(67_108_864, ge=1024, le=2_147_483_648)
    batch_feed_window: int = Field(12, ge=1, le=10_000)
    batch_max_running_per_tenant: int = Field(3, ge=1, le=10)
    asset_root: Path = Path("storage/assets")
    asset_max_upload_bytes: int = Field(2_147_483_648, ge=1024, le=10_995_116_277_760)
    asset_worker_hmac_secret: str = "development-only-change-me"
    asset_worker_lease_seconds: int = Field(300, ge=30, le=3600)
    asset_worker_heartbeat_timeout_seconds: int = Field(30, ge=5, le=600)
    asset_codex_probe_max_age_seconds: int = Field(3600, ge=300, le=86400)
    asset_worker_max_concurrency: int = Field(2, ge=1, le=32)
    asset_worker_max_load_per_cpu: float = Field(0.85, ge=0.1, le=4.0)
    asset_worker_min_available_memory_mb: int = Field(8192, ge=1024, le=1_048_576)
    asset_job_max_attempts: int = Field(2, ge=1, le=10)
    substance_pending_reservation_seconds: int = Field(60, ge=30, le=86_400)
    uv_qa_enforcement: Literal["strict", "advisory"] = "advisory"
    retopology_qa_enforcement: Literal["strict", "advisory"] = "advisory"

    @field_validator(
        "jwt_secret",
        "api_key_pepper",
        "node_agent_hmac_secret",
        "alertmanager_webhook_token",
    )
    @classmethod
    def production_secrets_must_change(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("secret cannot be empty")
        return value

    @model_validator(mode="after")
    def reject_development_secrets_in_production(self) -> "Settings":
        if self.environment.lower() == "production":
            protected = (
                self.jwt_secret,
                self.api_key_pepper,
                self.node_agent_hmac_secret,
                self.alertmanager_webhook_token,
            )
            if any(
                value == "development-only-change-me" or value.startswith("CHANGE_ME")
                for value in protected
            ):
                raise ValueError("production secrets must be replaced before startup")
        return self

    def node_agent_secret(self, node_id: str) -> str:
        per_node = {
            "worker-3090-a": self.node_agent_hmac_secret_worker_3090_a,
            "worker-3090-b": self.node_agent_hmac_secret_worker_3090_b,
            "control-4090": self.node_agent_hmac_secret_control_4090,
        }
        return per_node.get(node_id) or self.node_agent_hmac_secret

    @property
    def callback_hosts(self) -> set[str]:
        return {
            item.strip().lower() for item in self.allowed_callback_hosts.split(",") if item.strip()
        }

    @property
    def overflow_windows(self) -> tuple[tuple[time, time], ...]:
        """Parse comma-separated local-time windows such as ``22:00-06:00``."""
        windows: list[tuple[time, time]] = []
        for raw_window in self.overflow_4090_allowed_windows.split(","):
            value = raw_window.strip()
            if not value:
                continue
            try:
                start_raw, end_raw = value.split("-", 1)
                start_hour, start_minute = (int(part) for part in start_raw.split(":"))
                end_hour, end_minute = (int(part) for part in end_raw.split(":"))
                windows.append((time(start_hour, start_minute), time(end_hour, end_minute)))
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "OVERFLOW_4090_ALLOWED_WINDOWS must use HH:MM-HH:MM[,HH:MM-HH:MM]"
                ) from exc
        return tuple(windows)


@lru_cache
def get_settings() -> Settings:
    return Settings()
