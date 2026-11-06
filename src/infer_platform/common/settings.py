from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class FakeConfig(BaseModel):
    base_ms: float = Field(default=20, ge=0)
    per_item_ms: float = Field(default=2, ge=0)
    jitter_ms: float = Field(default=3, ge=0)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal["fake"]
    fake: FakeConfig = Field(default_factory=FakeConfig)
    max_input_chars: int = Field(default=4000, gt=0)
    request_timeout_ms: int = Field(default=5000, gt=0)
    max_queue_depth: int = Field(default=500, gt=0)


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

    def load_models(self) -> dict[str, ModelConfig]:
        with self.models_path.open() as source:
            return ModelCatalog.model_validate(yaml.safe_load(source)).models
