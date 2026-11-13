import base64
import json

import pytest
from pydantic import ValidationError

from infer_platform.common.inputs import (
    InferInput,
    InputError,
    InputSpec,
    default_specs,
    describe,
    normalize,
)
from infer_platform.common.schemas import RequestMessage
from infer_platform.common.settings import ModelConfig

TEXT = default_specs(10)
MULTI = {
    "question": InputSpec(type="text", max_chars=20),
    "features": InputSpec(type="tensor", dtype="float32", shape=[-1, 2], max_elements=8),
    "photo": InputSpec(type="image", max_bytes=8, required=False),
    "meta": InputSpec(type="json", max_bytes=30, required=False),
}


def body(**inputs: object) -> InferInput:
    return InferInput.model_validate({"inputs": inputs})


def test_legacy_body_is_unchanged_on_the_wire_and_for_runners() -> None:
    legacy = InferInput.model_validate_json('{"input":"hello"}')
    assert legacy.model_dump_json(exclude_none=True) == '{"input":"hello","options":{}}'
    assert normalize(legacy, TEXT) == {"input": "hello", "options": {}}


def test_exactly_one_of_input_or_inputs() -> None:
    for bad in ({}, {"input": "a", "inputs": {"input": {"type": "text", "value": "a"}}}):
        with pytest.raises(ValidationError):
            InferInput.model_validate(bad)


def test_typed_inputs_normalize_to_runner_values() -> None:
    photo = base64.b64encode(b"\x89PNG").decode()
    result = normalize(
        body(
            question={"type": "text", "value": "why?"},
            features={"type": "tensor", "dtype": "float32", "shape": [2, 2], "data": [1, 2, 3, 4]},
            photo={"type": "image", "data_b64": photo},
            meta={"type": "json", "value": {"k": [1, 2]}},
        ),
        MULTI,
    )
    assert result["question"] == "why?"
    assert result["features"] == {"dtype": "float32", "shape": [2, 2], "data": [1, 2, 3, 4]}
    assert result["photo"] == b"\x89PNG"
    assert result["meta"] == {"k": [1, 2]}


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        ({"question": {"type": "text", "value": "x" * 21}}, "exceeds 20 characters"),
        ({"features": {"type": "tensor", "dtype": "float32", "shape": [1, 2], "data": [1, 2]}},
         "missing inputs"),
        ({"question": {"type": "json", "value": 1}}, "must have type 'text'"),
        ({"question": {"type": "text", "value": "a"}, "extra": {"type": "text", "value": "a"}},
         "unknown inputs"),
    ],
)  # fmt: skip
def test_declaration_violations_are_input_errors(inputs: dict[str, object], message: str) -> None:
    features = {"type": "tensor", "dtype": "float32", "shape": [1, 2], "data": [1, 2]}
    supplied = (
        inputs if "features" in inputs or "extra" in inputs else {**inputs, "features": features}
    )
    with pytest.raises(InputError, match=message):
        normalize(body(**supplied), MULTI)


def test_tensor_shape_dtype_and_size_checks() -> None:
    base = {"question": {"type": "text", "value": "a"}}
    cases = [
        ({"dtype": "int64", "shape": [1, 2], "data": [1, 2]}, "dtype"),
        ({"dtype": "float32", "shape": [2, 3], "data": [0] * 6}, "shape"),
        ({"dtype": "float32", "shape": [5, 2], "data": [0] * 10}, "elements"),
    ]
    for tensor, message in cases:
        with pytest.raises(InputError, match=message):
            normalize(body(**base, features={"type": "tensor", **tensor}), MULTI)
    with pytest.raises(ValidationError, match="product of shape"):
        body(features={"type": "tensor", "dtype": "float32", "shape": [2, 2], "data": [1]})


def test_image_and_json_byte_limits_and_bad_base64() -> None:
    base = {
        "question": {"type": "text", "value": "a"},
        "features": {"type": "tensor", "dtype": "float32", "shape": [1, 2], "data": [1, 2]},
    }
    big = base64.b64encode(b"x" * 9).decode()
    with pytest.raises(InputError, match="exceeds 8 bytes"):
        normalize(body(**base, photo={"type": "image", "data_b64": big}), MULTI)
    with pytest.raises(InputError, match="not valid base64"):
        normalize(body(**base, photo={"type": "image", "data_b64": "@@@@"}), MULTI)
    with pytest.raises(InputError, match="exceeds 30 bytes"):
        normalize(body(**base, meta={"type": "json", "value": "y" * 40}), MULTI)


def test_legacy_body_needs_a_single_text_declaration() -> None:
    with pytest.raises(InputError, match="named 'inputs'"):
        normalize(InferInput(input="hi"), MULTI)
    renamed = {"prompt": InputSpec(type="text", max_chars=5)}
    assert normalize(InferInput(input="hi"), renamed) == {"prompt": "hi", "options": {}}


def test_model_config_validates_declarations_and_backends() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        ModelConfig(backend="fake", inputs={"options": {"type": "text", "max_chars": 1}})
    with pytest.raises(ValidationError, match="need max_chars"):
        ModelConfig(backend="fake", inputs={"q": {"type": "text"}})
    with pytest.raises(ValidationError, match="pipeline configuration"):
        ModelConfig(backend="hf_pipeline", hf_id="x")
    with pytest.raises(ValidationError, match="onnx configuration"):
        ModelConfig(backend="onnx")
    assert ModelConfig(backend="fake", max_input_chars=7).input_specs() == default_specs(7)
    assert describe(TEXT) == {"input": {"type": "text", "required": True, "max_chars": 10}}


def test_request_message_omits_empty_payload_ref() -> None:
    message = RequestMessage(
        request_id="r", reply_to="inbox:g", payload="{}", timeout_ms=1, key_id="k", trace_id="t"
    )
    assert "payload_ref" not in message.fields()
    checked = message.model_copy(update={"payload": "", "payload_ref": "blob:r"})
    assert checked.fields()["payload_ref"] == "blob:r"
    assert RequestMessage.model_validate(checked.fields()) == checked
    assert json.loads(InferInput(input="a").model_dump_json(exclude_none=True)) == {
        "input": "a",
        "options": {},
    }
