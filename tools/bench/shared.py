import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from infer_platform.common.settings import FakeConfig
from infer_platform.worker.runners.base import ModelRunner
from infer_platform.worker.runners.fake import FakeRunner
from infer_platform.worker.runners.torch_classifier import TorchClassifierRunner

TEXT = "The film was beautifully acted and I enjoyed the story from beginning to end."


def runner_for(backend: str, threads: int) -> ModelRunner:
    if backend == "fake":
        return FakeRunner(FakeConfig(base_ms=20, per_item_ms=2, jitter_ms=0))
    return TorchClassifierRunner(
        "distilbert-base-uncased-finetuned-sst-2-english",
        device="cpu",
        threads=threads,
        cache_dir=Path("/tmp/infer-model-cache"),
        local_files_only=True,
    )


def metadata(runner: ModelRunner) -> dict[str, Any]:
    import torch
    import transformers

    return {
        "date_utc": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "tracked_changes": subprocess.check_output(
            ["git", "diff", "--name-only"], text=True
        ).splitlines(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cpu_inventory": subprocess.check_output(["lscpu"], text=True),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "device": getattr(runner, "device", "simulated CPU sleep"),
        "model_revision": getattr(runner, "resolved_revision", None),
        "input": TEXT,
    }
