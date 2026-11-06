import asyncio
from typing import Any


class KubernetesScaler:
    def __init__(self, api: Any, namespace: str, deployments: dict[str, str]) -> None:
        self.api, self.namespace, self.deployments = api, namespace, deployments

    async def current(self, model: str, observed: int) -> int:
        result = await asyncio.to_thread(
            self.api.read_namespaced_deployment_scale,
            self.deployments[model],
            self.namespace,
            _request_timeout=(3, 5),
        )
        return int(result.spec.replicas)

    async def scale(self, model: str, replicas: int) -> None:
        await asyncio.to_thread(
            self.api.patch_namespaced_deployment_scale,
            self.deployments[model],
            self.namespace,
            {"spec": {"replicas": replicas}},
            _request_timeout=(3, 5),
        )
