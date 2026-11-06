import asyncio
import signal

import structlog
from prometheus_client import start_http_server

from infer_platform.autoscaler.loop import Controller
from infer_platform.autoscaler.scalers import Scaler
from infer_platform.autoscaler.scalers.dryrun import DryRunScaler
from infer_platform.common.clock import SystemClock
from infer_platform.common.logging import configure_logging
from infer_platform.common.redis import create_redis
from infer_platform.common.settings import Settings


async def main() -> None:
    configure_logging()
    cfg = Settings()
    policies = {
        name: model.autoscale
        for name, model in cfg.load_models().items()
        if model.autoscale is not None
    }
    scaler: Scaler = DryRunScaler()
    if cfg.scaler_mode == "kubernetes":
        from kubernetes import client, config

        from infer_platform.autoscaler.scalers.kubernetes import KubernetesScaler

        await asyncio.to_thread(config.load_incluster_config)
        scaler = KubernetesScaler(
            client.AppsV1Api(), cfg.kubernetes_namespace, cfg.worker_deployments
        )
        policies = {
            name: policy for name, policy in policies.items() if name in cfg.worker_deployments
        }
    redis = create_redis(cfg)
    controller = Controller(
        redis, policies, scaler, SystemClock(), cfg.autoscale_interval_s, cfg.autoscale_lock_ttl_s
    )
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, controller.stop.set)
    server, _ = await asyncio.to_thread(
        start_http_server, cfg.autoscaler_metrics_port, registry=controller.metrics.registry
    )
    try:
        await controller.run()
    except Exception:
        structlog.get_logger().exception("autoscaler_stopped", component="autoscaler")
        raise
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
