import random
import time
from typing import Any

from infer_platform.common.settings import FakeConfig


class FakeRunner:
    """Simulated cost, not a trained classifier; call only from an executor thread."""

    def __init__(self, config: FakeConfig) -> None:
        self.config = config

    def load(self) -> None:
        pass

    def warmup(self) -> None:
        pass

    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        delay = self.config.base_ms + self.config.per_item_ms * len(inputs)
        delay += random.uniform(0, self.config.jitter_ms)
        time.sleep(delay / 1000)
        return [{"label": "SIMULATED", "input_chars": len(item["input"])} for item in inputs]
