from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InferInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    input: str = Field(min_length=1)
    options: dict[str, Any] = Field(default_factory=dict)


class RequestMessage(BaseModel):
    request_id: str
    reply_to: str
    kind: Literal["infer", "chat"] = "infer"
    payload: str
    timeout_ms: int = Field(gt=0)
    stream: Literal["0", "1"] = "0"
    key_id: str
    trace_id: str

    def fields(self) -> dict[str, str]:
        return {key: str(value) for key, value in self.model_dump().items()}


class Meta(BaseModel):
    worker_id: str
    batch_size: int = 1
    queue_ms: float = 0
    batch_wait_ms: float = 0
    inference_ms: float = 0


class InboxMessage(BaseModel):
    request_id: str
    type: Literal["chunk", "final", "error"]
    data: str
    meta: str


class InferResponse(BaseModel):
    request_id: str
    output: dict[str, Any]
    meta: Meta


class Heartbeat(BaseModel):
    worker_id: str
    device: str
    backend: str
    in_flight: int
    busy_ratio: float
    started_at: float
    accepting: bool = True


class ResultRecord(BaseModel):
    status: Literal[
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED", "DEAD_LETTERED"
    ]
    key_id: str
    deadline_ms: float
    response: InboxMessage | None = None
    last_error: str | None = None
