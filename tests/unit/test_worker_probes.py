import asyncio

import httpx
from prometheus_client import CollectorRegistry

from infer_platform.worker.probes import start_server


async def test_worker_warmup_and_drain_readiness():
    ready, stop = asyncio.Event(), asyncio.Event()
    server = await asyncio.to_thread(start_server, 0, CollectorRegistry(), ready, stop)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
            ready.set()
            assert (await client.get("/readyz")).status_code == 200
            stop.set()
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/metrics")).status_code == 200
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
