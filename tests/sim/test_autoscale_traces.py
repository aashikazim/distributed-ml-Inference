from infer_platform.autoscaler.policy import Policy
from tools.autoscale_sim import simulate, trace


def test_sawtooth_stabilizes_and_stays_bounded():
    stable = simulate(trace("sawtooth"), Policy())
    unstable = simulate(
        trace("sawtooth"), Policy(stabilization_s=0, down_cooldown_s=0, up_cooldown_s=0)
    )
    assert all(1 <= r["desired"] <= 8 for r in stable["rows"])
    assert stable["summary"]["direction_reversals"] < unstable["summary"]["direction_reversals"]
    assert stable["summary"]["remaining"] == 0


def test_steady_trace_needs_one_replica():
    result = simulate(trace("steady"), Policy())
    assert max(r["desired"] for r in result["rows"]) == 1
    assert result["summary"]["remaining"] == 0
