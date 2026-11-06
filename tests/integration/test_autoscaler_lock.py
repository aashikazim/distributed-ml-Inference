import pytest

from infer_platform.autoscaler.loop import Controller
from infer_platform.autoscaler.policy import Policy
from infer_platform.autoscaler.scalers.dryrun import DryRunScaler
from infer_platform.common.clock import SystemClock
from infer_platform.common.redis import create_redis
from tools.reliability import settings_for

pytestmark = pytest.mark.integration


async def test_lock_owner_and_missing_worker_recommendation(redis_url, tmp_path):
    cfg = settings_for(tmp_path, redis_url)
    redis = create_redis(cfg)
    first = Controller(redis, {cfg.worker_model: Policy()}, DryRunScaler(), SystemClock())
    second = Controller(redis, {}, DryRunScaler(), SystemClock())
    try:
        assert await first.acquire()
        assert not await second.acquire()
        assert not await second.renew()
        await first.cycle()
        assert (
            first.metrics.registry.get_sample_value(
                "autoscaler_desired_replicas", {"model": cfg.worker_model}
            )
            == 1
        )
        await redis.set("autoscaler:lock", "new-owner")
        assert not await first.renew()
        with pytest.raises(RuntimeError, match="leadership lost"):
            await first.cycle()
        assert await redis.get("autoscaler:lock") == "new-owner"
    finally:
        await redis.delete("autoscaler:lock")
        await redis.aclose()
