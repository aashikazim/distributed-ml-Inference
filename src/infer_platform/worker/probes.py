import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from prometheus_client import CollectorRegistry, generate_latest


def start_server(
    port: int, registry: CollectorRegistry, ready: asyncio.Event, stop: asyncio.Event
) -> ThreadingHTTPServer:
    """Liveness is process-local; readiness follows model warmup and draining."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            status = 200
            if path == "/metrics":
                body, content_type = generate_latest(registry), "text/plain; version=0.0.4"
            elif path in {"/healthz", "/readyz"}:
                status = 503 if path == "/readyz" and (not ready.is_set() or stop.is_set()) else 200
                body, content_type = (
                    b'{"status":"ok"}' if status == 200 else b'{"status":"not_ready"}',
                    "application/json",
                )
            else:
                status, body, content_type = 404, b"not found", "text/plain"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("", port), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
