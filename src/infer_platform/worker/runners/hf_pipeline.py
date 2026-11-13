"""Generic Hugging Face ``pipeline`` runner: one class serves many model families.

The task comes from an allowlist in settings, the model id from trusted
configuration, remote code is never executed and weights must be safetensors.
Components are loaded explicitly so they honour the worker's cache directory and
offline flag, then handed to ``transformers.pipeline`` for pre/post-processing.
"""

import io
from pathlib import Path
from typing import Any

from infer_platform.common.inputs import InputSpec

AUTO_MODEL = {
    "text-classification": "AutoModelForSequenceClassification",
    "token-classification": "AutoModelForTokenClassification",
    "feature-extraction": "AutoModel",
    "image-classification": "AutoModelForImageClassification",
}
IMAGE_TASKS = {"image-classification"}


def jsonable(value: Any) -> Any:
    """Convert numpy/torch scalars and arrays inside pipeline output to JSON types."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return value


def mean_pool(token_vectors: list[list[float]]) -> list[float]:
    count = len(token_vectors)
    return [sum(column) / count for column in zip(*token_vectors, strict=True)]


class HFPipelineRunner:
    def __init__(
        self,
        task: str,
        hf_id: str,
        revision: str = "main",
        inputs: dict[str, InputSpec] | None = None,
        kwargs: dict[str, Any] | None = None,
        device: str = "auto",
        threads: int = 1,
        cache_dir: Path | None = None,
        local_files_only: bool = False,
    ) -> None:
        if task not in AUTO_MODEL:
            raise ValueError(f"unsupported pipeline task '{task}'")
        wanted = "image" if task in IMAGE_TASKS else "text"
        names = [name for name, spec in (inputs or {}).items() if spec.type == wanted]
        if len(names) != 1:
            raise ValueError(f"{task} needs exactly one declared {wanted} input")
        self.task, self.hf_id, self.revision = task, hf_id, revision
        self.input_name = names[0]
        self.kwargs = kwargs or {}
        self.requested_device, self.threads = device, threads
        self.cache_dir, self.local_files_only = cache_dir, local_files_only
        self.pipe: Any = None
        self.device = "cpu"

    def load(self) -> None:
        if self.pipe is not None:
            return
        import torch
        import transformers

        torch.set_num_threads(self.threads)
        self.device = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if self.requested_device == "auto"
            else self.requested_device
        )
        hub = {
            "revision": self.revision,
            "cache_dir": self.cache_dir,
            "local_files_only": self.local_files_only,
            "trust_remote_code": False,
        }
        model = getattr(transformers, AUTO_MODEL[self.task]).from_pretrained(
            self.hf_id, use_safetensors=True, **hub
        )
        if self.task in IMAGE_TASKS:
            processor = transformers.AutoImageProcessor.from_pretrained
            parts: dict[str, Any] = {
                "image_processor": processor(self.hf_id, **hub)  # type: ignore[no-untyped-call]
            }
        else:
            tokenizer = transformers.AutoTokenizer.from_pretrained
            parts = {"tokenizer": tokenizer(self.hf_id, **hub)}  # type: ignore[no-untyped-call]
        model.eval()
        build: Any = transformers.pipeline  # task is a runtime-allowlisted string
        self.pipe = build(self.task, model=model, device=self.device, **parts)

    def warmup(self) -> None:
        if self.task in IMAGE_TASKS:
            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", (32, 32)).save(buffer, format="PNG")
            self.predict_batch([{self.input_name: buffer.getvalue(), "options": {}}])
        else:
            self.predict_batch([{self.input_name: "Warmup.", "options": {}}])

    def cost(self, body: dict[str, Any]) -> float:
        """Text: estimated tokens. Images are resized to a fixed size, so cost 1."""
        if self.task in IMAGE_TASKS:
            return 1.0
        typed = (body.get("inputs") or {}).get(self.input_name) or {}
        text = body.get("input") or typed.get("value") or ""
        return float(len(text) // 4 + 2)

    def prepare(self, value: Any) -> Any:
        if self.task not in IMAGE_TASKS:
            return value
        from PIL import Image

        image = Image.open(io.BytesIO(value))
        image.load()  # decode now so corrupt files fail this item, not the batch
        return image.convert("RGB")

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import torch

        if self.pipe is None:
            raise RuntimeError("Model must be loaded before inference")
        if not inputs:
            return []
        values = [self.prepare(item[self.input_name]) for item in inputs]
        with torch.inference_mode():
            outputs = self.pipe(values, batch_size=len(values), **self.kwargs)
        results = []
        for output in outputs:
            output = jsonable(output)
            if self.task == "feature-extraction":
                # Pipeline returns [1][tokens][hidden]; mean pooling gives one vector.
                results.append({"embedding": mean_pool(output[0])})
            else:
                results.append({"predictions": output})
        return results
