"""Explicit trusted model download; tests and measurements use the local cache only."""

from pathlib import Path

import structlog

from infer_platform.worker.runners.torch_classifier import TorchClassifierRunner


def main() -> None:
    runner = TorchClassifierRunner(
        "distilbert-base-uncased-finetuned-sst-2-english",
        device="cpu",
        threads=1,
        cache_dir=Path("/tmp/infer-model-cache"),
    )
    runner.load()
    runner.warmup()
    structlog.get_logger().info(
        "model_prepared",
        model=runner.hf_id,
        revision=runner.resolved_revision,
        cache=str(runner.cache_dir),
        device=runner.device,
    )


if __name__ == "__main__":
    main()
