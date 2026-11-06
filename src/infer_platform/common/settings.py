from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FakeConfig(BaseModel):
    base_ms: float = Field(default=20, ge=0)
    per_item_ms: float = Field(default=2, ge=0)
    jitter_ms: float = Field(default=3, ge=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["fake", "torch"]
    hf_id: str | None = None
    revision: str = "main"
    task: Literal["classification"] = "classification"
    max_batch_size: int = Field(default=1, gt=0)
    max_wait_ms: float = Field(default=0, ge=0)
    prefetch_bound: int | None = Field(default=None, gt=0)
    reclaim_idle_ms: int = Field(default=30000, gt=0)
    reclaim_interval_ms: int = Field(default=2000, gt=0)
    max_deliveries: int = Field(default=3, ge=1)
    fake: FakeConfig = Field(default_factory=FakeConfig)
    max_input_chars: int = Field(default=4000, gt=0)
    request_timeout_ms: int = Field(default=5000, gt=0)
    max_queue_depth: int = Field(default=500, gt=0)

    @model_validator(mode="after")
    def validate_backend(self) -> "ModelConfig":
        if self.backend == "torch" and not self.hf_id:
            raise ValueError("torch backend requires a trusted hf_id")
        if self.prefetch_bound is not None and self.prefetch_bound < self.max_batch_size:
            raise ValueError("prefetch_bound must be at least max_batch_size")
        return self


class ModelCatalog(BaseModel):
    models: dict[str, ModelConfig]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFER_", extra="ignore")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    api_keys: list[SecretStr] = Field(min_length=1)
    models_path: Path = Path("config/models.yaml")
    worker_model: str = "fake-classifier"
    redis_timeout_s: float = Field(default=3, gt=1)
    inbox_ttl_s: int = Field(default=120, ge=10)
    max_gateway_inflight: int = Field(default=1000, gt=0)

    heartbeat_interval_s: float = Field(default=3, gt=0)
    heartbeat_ttl_s: int = Field(default=10, gt=0)
    result_ttl_s: int = Field(default=300, gt=0)
    cancel_ttl_s: int = Field(default=120, gt=0)
    rate_limit_per_s: float = Field(default=100, gt=0)
    rate_limit_burst: int = Field(default=200, gt=0)
    admission_enabled: bool = True
    max_body_bytes: int = Field(default=65536, gt=0)

    @model_validator(mode="after")
    def heartbeat_policy(self) -> "Settings":
        if self.heartbeat_interval_s >= self.heartbeat_ttl_s:
            raise ValueError("heartbeat interval must be shorter than TTL")
        return self

    device: Literal["auto", "cpu", "cuda"] = "auto"
    torch_threads: int = Field(default=1, gt=0)
    torch_fp16: bool = False
    model_cache: Path = Path("/tmp/infer-model-cache")
    model_local_files_only: bool = False

    def load_models(self) -> dict[str, ModelConfig]:
        with self.models_path.open() as source:
            return ModelCatalog.model_validate(yaml.safe_load(source)).models
