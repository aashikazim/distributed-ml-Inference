GROUP = "workers"


def request_stream(model: str) -> str:
    return f"req:{model}"


def inbox(gateway_id: str) -> str:
    return f"inbox:{gateway_id}"


def result_key(request_id: str) -> str:
    return f"result:{request_id}"


def cancel_key(request_id: str) -> str:
    return f"cancel:{request_id}"


def worker_key(model: str, worker_id: str) -> str:
    return f"worker:{model}:{worker_id}"


def dlq_key(model: str) -> str:
    return f"dlq:{model}"


def blob_key(request_id: str) -> str:
    """Claim-check storage for a large request payload (see ADR-0011)."""
    return f"blob:{request_id}"
