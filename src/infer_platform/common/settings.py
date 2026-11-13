from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from infer_platform.autoscaler.policy import Policy
from infer_platform.common.inputs import InputSpec, default_specs, validate_names


class FakeConfig(BaseModel):
    base_ms: float = Field(default=20, ge=0)
    per_item_ms: float = Field(default=2, ge=0)
    jitter_ms: float = Field(default=3, ge=0)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = "http://vllm:8000/v1"
    served_model: str = "Qwen/Qwen2.5-0.5B-Instruct"
    max_concurrent: int = Field(default=32, gt=0)
    max_tokens_cap: int = Field(default=512, gt=0)
    coalesce_ms: float = Field(default=20, ge=0)
    max_buffered_chunks: int = Field(default=128, gt=0)
    max_output_chars: int = Field(default=262144, gt=0)
    cancel_poll_ms: float = Field(default=200, gt=0)
    http_timeout_s: float = Field(default=60, gt=0)


PipelineTask = Literal[
    "text-classification",
    "token-classification",
    "feature-extraction",
    "image-classification",
    "zero-shot-classification",
]


class PipelineConfig(BaseModel):
    """Hugging Face ``pipeline`` backend. Tasks are allowlisted; no remote code."""

    model_config = ConfigDict(extra="forbid")
    task: PipelineTask
    kwargs: dict[str, str | int | float | bool] = Field(default_factory=dict)


class OnnxConfig(BaseModel):
    """ONNX Runtime backend. ``path`` comes only from trusted configuration."""

    model_config = ConfigDict(extra="forbid")
    path: Path
    outputs: list[str] | None = None
    intra_op_threads: int = Field(default=1, gt=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoscale: Policy | None = None
    backend: Literal["fake", "torch", "llm", "hf_pipeline", "onnx"]
    llm: LLMConfig | None = None
    pipeline: PipelineConfig | None = None
    onnx: OnnxConfig | None = None
    hf_id: str | None = None
    revision: str = "main"
    task: Literal["classification", "chat"] = "classification"
    # Typed inputs; None keeps the original single text field called "input".
    inputs: dict[str, InputSpec] | None = None
    max_batch_size: int = Field(default=1, gt=0)
    # Optional cost budget per batch (e.g. tokens); runners define an item's cost.
    max_batch_cost: float | None = Field(default=None, gt=0)
    # "padded": batch cost = size x largest item (dynamic padding); "sum": add costs.
    batch_cost_mode: Literal["padded", "sum"] = "padded"
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
        if self.backend == "llm" and self.llm is None:
            raise ValueError("llm backend requires llm configuration")
        if self.backend in {"torch", "hf_pipeline"} and not self.hf_id:
            raise ValueError(f"{self.backend} backend requires a trusted hf_id")
        if self.backend == "hf_pipeline" and self.pipeline is None:
            raise ValueError("hf_pipeline backend requires pipeline configuration")
        if self.backend == "onnx" and self.onnx is None:
            raise ValueError("onnx backend requires onnx configuration")
        if self.inputs is not None:
            validate_names(self.inputs)
        if self.prefetch_bound is not None and self.prefetch_bound < self.max_batch_size:
            raise ValueError("prefetch_bound must be at least max_batch_size")
        return self

    def input_specs(self) -> dict[str, InputSpec]:
        return self.inputs if self.inputs is not None else default_specs(self.max_input_chars)


class ModelCatalog(BaseModel):
    models: dict[str, ModelConfig]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INFER_", extra="ignore")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    api_keys: list[SecretStr] = Field(min_length=1)
    models_path: Path = Path("config/models.yaml")
    worker_model: str = "fake-classifier"
    redis_max_connections: int = Field(default=64, ge=2)
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
    # Payloads larger than this travel as a blob key instead of inside the stream entry.
    claim_check_bytes: int = Field(default=16384, gt=0)
    # How long a gateway trusts its cached list of live workers before rescanning.
    worker_cache_s: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def heartbeat_policy(self) -> "Settings":
        if self.heartbeat_interval_s >= self.heartbeat_ttl_s:
            raise ValueError("heartbeat interval must be shorter than TTL")
        return self

    llm_base_url: str | None = None
    gateway_stream_buffer: int = Field(default=128, ge=2)
    worker_metrics_port: int = Field(default=9001, ge=0, le=65535)

    scaler_mode: Literal["dryrun", "kubernetes"] = "dryrun"
    autoscale_interval_s: float = Field(default=5, gt=0)
    autoscale_lock_ttl_s: float = Field(default=30, ge=15)
    autoscaler_metrics_port: int = Field(default=9003, ge=0, le=65535)
    kubernetes_namespace: str = "infer-platform"
    worker_deployments: dict[str, str] = Field(default_factory=dict)

    exporter_interval_s: float = Field(default=2, gt=0)

    device: Literal["auto", "cpu", "cuda"] = "auto"
    torch_threads: int = Field(default=1, gt=0)
    torch_fp16: bool = False
    model_cache: Path = Path("/tmp/infer-model-cache")
    model_local_files_only: bool = False

    def load_models(self) -> dict[str, ModelConfig]:
        with self.models_path.open() as source:
            return ModelCatalog.model_validate(yaml.safe_load(source)).models
