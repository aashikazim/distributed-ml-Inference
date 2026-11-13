import io
from pathlib import Path
from typing import Any

import pytest

from infer_platform.common.inputs import InputSpec
from infer_platform.common.settings import FakeConfig, ModelConfig, Settings
from infer_platform.worker.runners.base import CostedRunner
from infer_platform.worker.runners.fake import FakeRunner
from infer_platform.worker.runners.registry import backends, create_runner
from tools.learning.onnx_double import write_double_model

FEATURES = {"x": InputSpec(type="tensor", dtype="float32", shape=[3], max_elements=3)}


def settings(tmp_path: Path) -> Settings:
    return Settings(api_keys=["k"], model_cache=tmp_path)  # type: ignore[list-item]


def test_registry_knows_every_non_llm_backend(tmp_path: Path) -> None:
    assert backends() == ["fake", "hf_pipeline", "onnx", "torch"]
    assert isinstance(create_runner(ModelConfig(backend="fake"), settings(tmp_path)), FakeRunner)
    with pytest.raises(ValueError, match="adapter"):
        create_runner(
            ModelConfig(backend="llm", llm={"base_url": "http://x/v1"}), settings(tmp_path)
        )


def test_fake_runner_keeps_legacy_output_and_describes_typed_inputs() -> None:
    runner = FakeRunner(FakeConfig(base_ms=0, per_item_ms=0, jitter_ms=0))
    legacy, typed = runner.predict_batch(
        [{"input": "abc", "options": {}}, {"photo": b"x", "question": "q", "options": {}}]
    )
    assert legacy == {"label": "SIMULATED", "input_chars": 3}
    assert typed == {"label": "SIMULATED", "inputs": ["photo", "question"]}


def test_onnx_runner_batches_tensors_and_reports_named_outputs(tmp_path: Path) -> None:
    write_double_model(tmp_path / "double.onnx")
    config = ModelConfig(backend="onnx", onnx={"path": "double.onnx"}, inputs=FEATURES)
    runner = create_runner(config, settings(tmp_path))  # relative path -> model cache
    runner.load()
    runner.warmup()
    items = [
        {"x": {"dtype": "float32", "shape": [3], "data": [1, 2, 3]}, "options": {}},
        {"x": {"dtype": "float32", "shape": [3], "data": [0.5, 0, -1]}, "options": {}},
    ]
    assert runner.predict_batch(items) == [{"y": [2, 4, 6]}, {"y": [1, 0, -2]}]
    assert isinstance(runner, CostedRunner)
    assert runner.cost({"inputs": {"x": {"data": [1, 2, 3]}}}) == 3


def test_onnx_runner_rejects_shapes_it_cannot_stack(tmp_path: Path) -> None:
    from infer_platform.worker.runners.onnx import OnnxRunner

    runner = OnnxRunner(write_double_model(tmp_path / "d.onnx"), FEATURES)
    runner.load()
    mixed = [
        {"x": {"dtype": "float32", "shape": [3], "data": [1, 2, 3]}},
        {"x": {"dtype": "float32", "shape": [2], "data": [1, 2]}},
    ]
    with pytest.raises(ValueError):
        runner.predict_batch(mixed)  # the batcher then retries each item alone
    with pytest.raises(ValueError, match="not in ONNX graph"):
        OnnxRunner(tmp_path / "d.onnx", {"z": FEATURES["x"]}).load()


class StubPipeline:
    def __init__(self, outputs: list[Any]) -> None:
        self.outputs, self.calls = outputs, []

    def __call__(self, values: list[Any], batch_size: int, **kwargs: Any) -> list[Any]:
        self.calls.append((values, batch_size, kwargs))
        return self.outputs


def test_hf_pipeline_runner_shapes_outputs_without_downloading() -> None:
    np = pytest.importorskip("numpy")
    from infer_platform.worker.runners.hf_pipeline import HFPipelineRunner

    text = {"text": InputSpec(type="text", max_chars=10)}
    runner = HFPipelineRunner("text-classification", "m", inputs=text, kwargs={"top_k": 1})
    runner.pipe = StubPipeline([[{"label": "POS", "score": np.float32(0.5)}]])
    assert runner.predict_batch([{"text": "hi", "options": {}}]) == [
        {"predictions": [{"label": "POS", "score": 0.5}]}
    ]
    assert runner.pipe.calls == [(["hi"], 1, {"top_k": 1})]

    embed = HFPipelineRunner("feature-extraction", "m", inputs=text)
    embed.pipe = StubPipeline([np.array([[[1.0, 3.0], [3.0, 5.0]]])])
    assert embed.predict_batch([{"text": "hi"}]) == [{"embedding": [2.0, 4.0]}]
    assert embed.cost({"input": "x" * 40}) == 12


def test_hf_pipeline_runner_decodes_images_in_the_runner() -> None:
    image_module = pytest.importorskip("PIL.Image")
    from infer_platform.worker.runners.hf_pipeline import HFPipelineRunner

    runner = HFPipelineRunner(
        "image-classification", "m", inputs={"photo": InputSpec(type="image", max_bytes=10**6)}
    )
    runner.pipe = StubPipeline([[{"label": "cat", "score": 0.9}]])
    buffer = io.BytesIO()
    image_module.new("L", (4, 4)).save(buffer, format="PNG")
    runner.predict_batch([{"photo": buffer.getvalue()}])
    decoded = runner.pipe.calls[0][0][0]
    assert decoded.mode == "RGB" and decoded.size == (4, 4)
    with pytest.raises(Exception):  # noqa: B017 - PIL raises its own error types
        runner.predict_batch([{"photo": b"not an image"}])
    with pytest.raises(ValueError, match="exactly one declared image input"):
        HFPipelineRunner(
            "image-classification", "m", inputs={"t": InputSpec(type="text", max_chars=1)}
        )
