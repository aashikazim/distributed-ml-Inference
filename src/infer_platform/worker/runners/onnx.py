"""Framework-agnostic runner for ONNX models with tensor inputs.

Any model exported to ONNX (PyTorch, TensorFlow, scikit-learn via converters, ...)
can be served if its inputs are declared as ``tensor`` inputs whose names match the
graph's input names. Each item's tensor excludes the batch dimension; the runner
stacks items along a new leading axis. Items with different shapes cannot share a
tensor, so the batch call fails and the batcher's per-item retry runs them alone.
"""

from pathlib import Path
from typing import Any

from infer_platform.common.inputs import InputSpec


class OnnxRunner:
    def __init__(
        self,
        path: Path,
        inputs: dict[str, InputSpec],
        outputs: list[str] | None = None,
        threads: int = 1,
    ) -> None:
        tensors = {name: spec for name, spec in inputs.items() if spec.type == "tensor"}
        if len(tensors) != len(inputs):
            raise ValueError("onnx backend accepts only tensor inputs")
        self.path, self.inputs, self.outputs, self.threads = path, tensors, outputs, threads
        self.session: Any = None
        self.output_names: list[str] = []
        self.device = "cpu"

    def load(self) -> None:
        if self.session is not None:
            return
        import onnxruntime as ort  # type: ignore[import-untyped]

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.threads
        self.session = ort.InferenceSession(
            str(self.path), options, providers=["CPUExecutionProvider"]
        )
        graph_inputs = {node.name for node in self.session.get_inputs()}
        missing = set(self.inputs) - graph_inputs
        if missing:
            raise ValueError(f"declared inputs not in ONNX graph: {sorted(missing)}")
        available = [node.name for node in self.session.get_outputs()]
        self.output_names = self.outputs or available
        unknown = set(self.output_names) - set(available)
        if unknown:
            raise ValueError(f"configured outputs not in ONNX graph: {sorted(unknown)}")

    def warmup(self) -> None:
        if all(spec.shape and -1 not in spec.shape for spec in self.inputs.values()):
            sample: dict[str, Any] = {"options": {}}
            for name, spec in self.inputs.items():
                assert spec.shape is not None and spec.dtype is not None
                size = 1
                for dimension in spec.shape:
                    size *= dimension
                sample[name] = {"dtype": spec.dtype, "shape": spec.shape, "data": [0] * size}
            self.predict_batch([sample])

    def cost(self, body: dict[str, Any]) -> float:
        """Total tensor elements across inputs."""
        values = (body.get("inputs") or {}).values()
        return float(sum(len(value.get("data", [])) for value in values)) or 1.0

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import numpy as np

        if self.session is None:
            raise RuntimeError("Model must be loaded before inference")
        if not inputs:
            return []
        feed = {}
        for name in self.inputs:
            arrays = [
                np.asarray(item[name]["data"], dtype=item[name]["dtype"]).reshape(
                    item[name]["shape"]
                )
                for item in inputs
            ]
            feed[name] = np.stack(arrays)  # raises on mismatched shapes -> per-item retry
        results = self.session.run(self.output_names, feed)
        named = list(zip(self.output_names, results, strict=True))
        return [
            {name: result[index].tolist() for name, result in named} for index in range(len(inputs))
        ]
