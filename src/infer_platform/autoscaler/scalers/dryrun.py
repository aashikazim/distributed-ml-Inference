import structlog


class DryRunScaler:
    """Log proposals without pretending that Compose replica counts changed."""

    async def current(self, model: str, observed: int) -> int:
        return observed

    async def scale(self, model: str, replicas: int) -> None:
        structlog.get_logger().info(
            "dry_run_scale", component="autoscaler", model=model, desired=replicas
        )
