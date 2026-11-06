import os

import pytest
from testcontainers.community.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_url():
    external = os.environ.get("INFER_TEST_REDIS_URL")
    if external:
        yield external
    else:
        with RedisContainer("redis:7.4") as container:
            yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"


@pytest.fixture(scope="session")
def sentiment_runner():
    from pathlib import Path

    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    from infer_platform.worker.runners.torch_classifier import TorchClassifierRunner

    runner = TorchClassifierRunner(
        "distilbert-base-uncased-finetuned-sst-2-english",
        device="cpu",
        threads=1,
        cache_dir=Path("/tmp/infer-model-cache"),
        local_files_only=True,
    )
    try:
        runner.load()
    except OSError:
        pytest.skip("Real-model weights not cached; tests never download models")
    runner.warmup()
    return runner
