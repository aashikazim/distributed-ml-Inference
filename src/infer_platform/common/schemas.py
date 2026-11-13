from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from infer_platform.common.inputs import InferInput

__all__ = ["InferInput"]  # re-exported: the HTTP body now lives in common.inputs


class RequestMessage(BaseModel):
    request_id: str
    reply_to: str
    kind: Literal["infer", "chat"] = "infer"
    payload: str
    timeout_ms: int = Field(gt=0)
    stream: Literal["0", "1"] = "0"
    key_id: str
    trace_id: str
    # Claim-check: when set, the real payload lives at this blob key and payload is "".
    payload_ref: str = ""

    def fields(self) -> dict[str, str]:
        # Omit an empty payload_ref so ordinary entries keep the original field set.
        return {
            key: str(value)
            for key, value in self.model_dump().items()
            if key != "payload_ref" or value
        }


class Meta(BaseModel):
    worker_id: str
    batch_size: int = 1
    queue_ms: float = 0
    batch_wait_ms: float = 0
    inference_ms: float = 0
    total_ms: float | None = None
    gateway_overhead_ms: float | None = None


class InboxMessage(BaseModel):
    request_id: str
    type: Literal["chunk", "final", "error"]
    data: str
    meta: str

    def fields(self) -> dict[str, str]:
        return {key: str(value) for key, value in self.model_dump().items()}


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
    stream_started: bool = False


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    messages: list[ChatMessage] = Field(min_length=1, max_length=64)
    max_tokens: int = Field(default=128, ge=1)
    temperature: float = Field(default=1, ge=0, le=2)
    stream: bool = False


class Usage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class Delta(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None


class ChunkChoice(BaseModel):
    index: Literal[0] = 0
    delta: Delta
    finish_reason: str | None = None


class ChatChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChunkChoice] = Field(max_length=1)
    usage: Usage | None = None


class ChunkBundle(BaseModel):
    chunks: list[ChatChunk]


class LLMMeta(Meta):
    ttft_ms: float | None = None
    tpot_ms: float | None = None
    completion_tokens: int | None = None
    chunk_writes: int = 0


class CompletionChoice(BaseModel):
    index: Literal[0] = 0
    message: ChatMessage
    finish_reason: str


class ChatCompletion(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: Usage | None = None
