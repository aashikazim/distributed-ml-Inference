from pathlib import Path
from typing import Any


class TorchClassifierRunner:
    """Load only trusted allowlisted models; require safetensors and no remote code."""

    def __init__(
        self,
        hf_id: str,
        revision: str = "main",
        device: str = "auto",
        threads: int = 1,
        fp16: bool = False,
        cache_dir: Path | None = None,
        local_files_only: bool = False,
    ) -> None:
        self.hf_id = hf_id
        self.revision = revision
        self.requested_device = device
        self.threads = threads
        self.fp16 = fp16
        self.cache_dir = cache_dir
        self.local_files_only = local_files_only
        self.model: Any = None
        self.tokenizer: Any = None
        self.device = "cpu"
        self.resolved_revision: str | None = None

    def load(self) -> None:
        if self.model is not None:
            return
        # Lazy imports keep the fake-only path independent of heavy ML dependencies.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(self.threads)
        self.device = (
            ("cuda" if torch.cuda.is_available() else "cpu")
            if self.requested_device == "auto"
            else self.requested_device
        )
        if self.fp16 and self.device != "cuda":
            raise ValueError("fp16 is supported only on CUDA")
        options = {
            "revision": self.revision,
            "cache_dir": self.cache_dir,
            "local_files_only": self.local_files_only,
            "trust_remote_code": False,
        }
        self.tokenizer = AutoTokenizer.from_pretrained(self.hf_id, **options)  # type: ignore[no-untyped-call]
        self.model = AutoModelForSequenceClassification.from_pretrained(
            self.hf_id,
            use_safetensors=True,
            **options,
        ).to(self.device)
        if self.fp16:
            self.model.half()
        self.model.eval()
        self.resolved_revision = getattr(self.model.config, "_commit_hash", None)

    def cost(self, body: dict[str, Any]) -> float:
        """Estimated tokens after truncation (~4 characters per token)."""
        text = body.get("input") or ""
        return float(min(256, len(text) // 4 + 2))

    def warmup(self) -> None:
        self.predict_batch([{"input": "Warmup."}])

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        import torch

        if self.model is None:
            raise RuntimeError("Model must be loaded before inference")
        if not inputs:
            return []
        encoded = self.tokenizer(
            [item["input"] for item in inputs],
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            logits = self.model(**encoded).logits
            probabilities = logits.float().softmax(dim=-1).cpu().tolist()
        return [
            {
                "label": self.model.config.id2label[max(range(len(row)), key=row.__getitem__)],
                "score": max(row),
                "probabilities": row,
            }
            for row in probabilities
        ]
