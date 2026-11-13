from typing import Any, Protocol, runtime_checkable


class ModelRunner(Protocol):
    """Synchronous model lifecycle; every method runs in the dedicated executor.

    ``predict_batch`` receives one dict per item: each declared input name mapped to
    its value (str, JSON, tensor dict or image bytes) plus ``"options"``. A model
    without an input declaration receives ``{"input": str, "options": {...}}``.
    """

    def load(self) -> None: ...
    def warmup(self) -> None: ...
    def predict_batch(self, inputs: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


@runtime_checkable
class CostedRunner(Protocol):
    """Optional: estimate an item's cost from its wire body for ``max_batch_cost``.

    Called on the event loop at accept time, so it must be cheap and must not
    load models. Without it, the worker uses the payload's size in bytes.
    """

    def cost(self, body: dict[str, Any]) -> float: ...
