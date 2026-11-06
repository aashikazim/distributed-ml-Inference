import logging
from collections.abc import MutableMapping
from typing import Any

import structlog


def context_fields(
    logger: Any, method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in ("request_id", "trace_id", "model", "worker_id", "component"):
        event.setdefault(key, None)
    return event


def configure_logging() -> None:
    logging.basicConfig(level=logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            context_fields,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
    )
