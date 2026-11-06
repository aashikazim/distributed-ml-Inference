from types import SimpleNamespace

import pytest

from infer_platform.autoscaler.policy import History, Policy, State, decide
from infer_platform.autoscaler.scalers.kubernetes import KubernetesScaler


@pytest.mark.parametrize(
    "state,expected",
    [
        (State(1, 0, 0, 0), 1),
        (State(1, 40, 0, 0), 2),
        (State(4, 0, 0.9, 0), 6),
        (State(2, 0, 0, 1), 3),
        (State(8, 9999, 1, 10), 8),
        (State(0, 0, 0, 0), 1),
        (State(4, 0, 0, 0), 1),
    ],
)
def test_decision_signals_and_bounds(state, expected):
    result = decide(state, Policy(), History(), 100)
    assert result.desired == expected
    assert History().recommendations == ()


def test_cooldown_and_stabilization_decay():
    policy = Policy()
    history = History(((0, 4),), 0)
    assert decide(State(4, 0, 0, 0), policy, history, 119).desired == 4
    assert decide(State(4, 0, 0, 0), policy, history, 121).desired == 1
    assert decide(State(2, 100, 1, 1), policy, History(last_change=99), 100).desired == 2
    assert decide(State(4, 0, 0, 0), policy, History(last_change=99), 100).desired == 4
    # Old high recommendations prevent shrinking; they must not cause growth.
    assert decide(State(2, 0, 0, 0), policy, History(((90, 8),)), 100).desired == 2


async def test_kubernetes_adapter_scale_subresource_and_timeout():
    calls = []

    class API:
        def read_namespaced_deployment_scale(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(spec=SimpleNamespace(replicas=2))

        def patch_namespaced_deployment_scale(self, *args, **kwargs):
            calls.append((args, kwargs))

    scaler = KubernetesScaler(API(), "test", {"fake": "worker-fake"})
    assert await scaler.current("fake", 0) == 2
    await scaler.scale("fake", 3)
    assert calls[1][0] == ("worker-fake", "test", {"spec": {"replicas": 3}})
    assert all(c[1]["_request_timeout"] == (3, 5) for c in calls)
