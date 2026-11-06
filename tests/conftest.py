import os

import pytest
from testcontainers.community.redis import RedisContainer


@pytest.fixture(scope="session")
def redis_url():
    external = os.environ.get("INFER_TEST_REDIS_URL")
    if external:
        yield external
    else:
        with RedisContainer("redis:7.4") as container:
            yield f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"
