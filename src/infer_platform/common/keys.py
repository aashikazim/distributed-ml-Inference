GROUP = "workers"


def request_stream(model: str) -> str:
    return f"req:{model}"


def inbox(gateway_id: str) -> str:
    return f"inbox:{gateway_id}"
