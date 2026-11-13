"""Typed, multi-input request contract shared by the gateway and workers.

A request carries either the legacy single text field ``{"input": "..."}`` or named
typed values ``{"inputs": {"name": {"type": ...}}}``. Each model declares the inputs
it accepts in ``config/models.yaml``; a model without a declaration accepts one text
input called ``input``, which is exactly the original contract.

``normalize`` validates a request against the declaration and produces the plain
dict handed to ``ModelRunner.predict_batch``: ``{name: value, ..., "options": {...}}``.
Values are JSON-native (str, JSON, tensor dict) or ``bytes`` for images, so the
expensive decode (pixels, numpy) happens inside the runner's executor thread.
"""

import base64
import binascii
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RESERVED_NAMES = frozenset({"options"})
TensorDType = Literal["float32", "float64", "int32", "int64", "bool"]


class InputError(ValueError):
    """The request does not match the model's declared inputs (HTTP 422)."""


# ---------------------------------------------------------------- wire values


class TextValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"]
    value: str = Field(min_length=1)


class JsonValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["json"]
    value: Any


class TensorValue(BaseModel):
    """Dense row-major tensor; ``data`` holds prod(shape) scalars."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["tensor"]
    dtype: TensorDType
    shape: list[Annotated[int, Field(ge=0)]] = Field(max_length=8)
    data: list[float | int | bool]

    @model_validator(mode="after")
    def size_matches_shape(self) -> "TensorValue":
        if len(self.data) != math.prod(self.shape):
            raise ValueError("tensor data length must equal the product of shape")
        return self


class ImageValue(BaseModel):
    """Encoded image file (PNG, JPEG, ...) as base64. Decoding happens in the runner."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["image"]
    data_b64: str = Field(min_length=1)


TypedValue = Annotated[
    TextValue | JsonValue | TensorValue | ImageValue, Field(discriminator="type")
]


class InferInput(BaseModel):
    """HTTP body of ``POST /v1/infer/{model}``: legacy ``input`` or typed ``inputs``."""

    model_config = ConfigDict(extra="forbid")
    input: str | None = Field(default=None, min_length=1)
    inputs: dict[str, TypedValue] | None = Field(default=None, min_length=1, max_length=16)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def exactly_one_form(self) -> "InferInput":
        if (self.input is None) == (self.inputs is None):
            raise ValueError("provide exactly one of 'input' or 'inputs'")
        return self


# ------------------------------------------------------------ declarations


class InputSpec(BaseModel):
    """One declared input in ``models.yaml``. Limits apply per request."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "json", "tensor", "image"]
    required: bool = True
    max_chars: int | None = Field(default=None, gt=0)
    max_bytes: int | None = Field(default=None, gt=0)
    dtype: TensorDType | None = None
    # -1 means "any size" in that dimension; None means "any shape".
    shape: list[int] | None = None
    max_elements: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def limits_fit_type(self) -> "InputSpec":
        if self.type == "text" and self.max_chars is None:
            raise ValueError("text inputs need max_chars")
        if self.type in {"json", "image"} and self.max_bytes is None:
            raise ValueError(f"{self.type} inputs need max_bytes")
        if self.type == "tensor" and (self.dtype is None or self.max_elements is None):
            raise ValueError("tensor inputs need dtype and max_elements")
        return self


def default_specs(max_input_chars: int) -> dict[str, InputSpec]:
    """The original contract: a single text field called ``input``."""
    return {"input": InputSpec(type="text", max_chars=max_input_chars)}


def validate_names(specs: dict[str, InputSpec]) -> None:
    if not specs:
        raise ValueError("a model must declare at least one input")
    reserved = RESERVED_NAMES & specs.keys()
    if reserved:
        raise ValueError(f"reserved input names: {sorted(reserved)}")


# ---------------------------------------------------------------- normalize


def _check(name: str, value: TypedValue, spec: InputSpec) -> Any:
    if value.type != spec.type:
        raise InputError(f"input '{name}' must have type '{spec.type}'")
    if isinstance(value, TextValue):
        assert spec.max_chars is not None
        if len(value.value) > spec.max_chars:
            raise InputError(f"input '{name}' exceeds {spec.max_chars} characters")
        return value.value
    if isinstance(value, JsonValue):
        assert spec.max_bytes is not None
        if len(json.dumps(value.value, separators=(",", ":"))) > spec.max_bytes:
            raise InputError(f"input '{name}' exceeds {spec.max_bytes} bytes")
        return value.value
    if isinstance(value, TensorValue):
        assert spec.max_elements is not None
        if value.dtype != spec.dtype:
            raise InputError(f"input '{name}' must have dtype '{spec.dtype}'")
        if len(value.data) > spec.max_elements:
            raise InputError(f"input '{name}' exceeds {spec.max_elements} elements")
        if spec.shape is not None and (
            len(spec.shape) != len(value.shape)
            or any(want not in (-1, got) for want, got in zip(spec.shape, value.shape, strict=True))
        ):
            raise InputError(f"input '{name}' must have shape {spec.shape}")
        return {"dtype": value.dtype, "shape": value.shape, "data": value.data}
    assert spec.max_bytes is not None
    # Reject on the encoded size first so a huge string is never decoded.
    if len(value.data_b64) > 4 * math.ceil(spec.max_bytes / 3):
        raise InputError(f"input '{name}' exceeds {spec.max_bytes} bytes")
    try:
        raw = base64.b64decode(value.data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InputError(f"input '{name}' is not valid base64") from exc
    if len(raw) > spec.max_bytes:
        raise InputError(f"input '{name}' exceeds {spec.max_bytes} bytes")
    return raw


def typed_inputs(body: InferInput, specs: dict[str, InputSpec]) -> dict[str, TypedValue]:
    """Map the legacy single-text form onto the model's only text input."""
    if body.inputs is not None:
        return body.inputs
    assert body.input is not None
    if len(specs) != 1 or next(iter(specs.values())).type != "text":
        raise InputError("this model needs named 'inputs'; see GET /v1/models")
    return {next(iter(specs)): TextValue(type="text", value=body.input)}


def normalize(body: InferInput, specs: dict[str, InputSpec]) -> dict[str, Any]:
    """Validate against the declaration; return the runner's input dict."""
    supplied = typed_inputs(body, specs)
    unknown = supplied.keys() - specs.keys()
    if unknown:
        raise InputError(f"unknown inputs: {sorted(unknown)}")
    missing = [name for name, spec in specs.items() if spec.required and name not in supplied]
    if missing:
        raise InputError(f"missing inputs: {missing}")
    values = {name: _check(name, value, specs[name]) for name, value in supplied.items()}
    return {**values, "options": body.options}


def describe(specs: dict[str, InputSpec]) -> dict[str, dict[str, Any]]:
    """Public, JSON-safe view of a declaration for ``GET /v1/models``."""
    return {name: spec.model_dump(exclude_none=True) for name, spec in specs.items()}
