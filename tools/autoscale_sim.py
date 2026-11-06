"""Deterministic fluid-queue model; all capacity and cold starts are simulated."""

import argparse
import json
import platform
import subprocess
import time
from collections import deque
from pathlib import Path

import structlog
import yaml

from infer_platform.autoscaler.policy import History, Policy, State, decide


def trace(name: str) -> list[float]:
    config = yaml.safe_load(Path(f"tools/traces/{name}.yaml").read_text())
    values = []
    for segment in config["segments"]:
        count = segment["seconds"]
        values.extend(
            segment["start_rps"]
            + (segment["end_rps"] - segment["start_rps"]) * n / max(1, count - 1)
            for n in range(count)
        )
    return values * config.get("repeat", 1)


def simulate(rates: list[float], policy: Policy, capacity: float = 20, warmup_s: int = 5) -> dict:
    queue: deque[list[float]] = deque()
    ready_at = [0] * policy.min
    desired = policy.min
    history = History()
    rows = []
    waits = []
    completed = 0.0
    busy = 0.0
    for now, rate in enumerate(rates):
        queue.append([float(now), rate])
        ready = sum(t <= now for t in ready_at)
        budget = ready * capacity
        served = 0.0
        while budget > 1e-9 and queue:
            arrived, amount = queue[0]
            count = min(amount, budget)
            waits.append((now - arrived + 1 / capacity, count))
            completed += count
            served += count
            budget -= count
            queue[0][1] -= count
            if queue[0][1] <= 1e-9:
                queue.popleft()
        busy = served / (ready * capacity) if ready else 0
        backlog = sum(item[1] for item in queue)
        age = now - queue[0][0] if queue else 0
        if now % 5 == 0:
            decision = decide(
                State(desired, int(backlog + 0.999999), busy, age, ready),
                policy,
                history,
                float(now),
            )
            history = decision.history
            if decision.desired > desired:
                ready_at.extend([now + warmup_s] * (decision.desired - desired))
            elif decision.desired < desired:
                ready_at = sorted(ready_at)[: decision.desired]
            desired = decision.desired
        rows.append(
            {
                "second": now,
                "arrival_rps": rate,
                "backlog": backlog,
                "head_age_s": age,
                "desired": desired,
                "ready": ready,
                "busy": busy,
            }
        )

    def quantile(q: float) -> float:
        cumulative = 0.0
        for value, weight in sorted(waits):
            cumulative += weight
            if cumulative >= completed * q:
                return value
        return 0.0

    changes = [
        b["desired"] - a["desired"]
        for a, b in zip(rows, rows[1:], strict=False)
        if b["desired"] != a["desired"]
    ]
    last_sign = 0
    reversals = 0
    for change in changes:
        sign = 1 if change > 0 else -1
        if last_sign and sign != last_sign:
            reversals += 1
        last_sign = sign
    return {
        "policy": policy.model_dump(),
        "rows": rows,
        "summary": {
            "completed": completed,
            "offered": sum(rates),
            "remaining": rows[-1]["backlog"],
            "peak_backlog": max(r["backlog"] for r in rows),
            "replica_seconds": sum(r["desired"] for r in rows),
            "wait_mean_s": sum(v * w for v, w in waits) / completed,
            "wait_p95_s": quantile(0.95),
            "changes": len(changes),
            "direction_reversals": reversals,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/results/autoscale-sim.json"))
    args = parser.parse_args()
    results = {}
    for name in ("steady", "ramp", "spike", "sawtooth"):
        results[name] = simulate(trace(name), Policy())
    for window in (0, 120):
        results[f"E9-window-{window}"] = simulate(
            trace("sawtooth"), Policy(stabilization_s=window, down_cooldown_s=0, up_cooldown_s=0)
        )
    for target in (5, 20, 80, 500):
        results[f"E10-target-{target}"] = simulate(
            trace("spike"), Policy(target_backlog_per_replica=target)
        )
    report = {
        "simulated": True,
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "assumptions": {
            "capacity_rps_per_ready_replica": 20,
            "warmup_seconds": 5,
            "step_seconds": 1,
            "control_interval_seconds": 5,
            "deadlines_or_admission": False,
        },
        "prediction": (
            "Stabilization reduces reversals but retains replicas; tighter backlog targets "
            "should reduce wait while consuming more replica-seconds."
        ),
        "results": results,
    }
    spike = results["spike"]["rows"]
    report["E8"] = {
        "spike_start_s": 60,
        "spike_end_s": 120,
        "first_zero_backlog_after_spike_s": next(
            (r["second"] for r in spike if r["second"] >= 120 and r["backlog"] < 1e-6), None
        ),
    }
    recovered = next((r["second"] for r in spike if r["second"] > 60 and r["backlog"] < 1e-6), None)
    report["E8"]["first_zero_backlog_after_spike_start_s"] = recovered
    report["E8"]["time_to_recover_s"] = recovered - 60 if recovered is not None else None
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(12, 10))
    for row, name in enumerate(("steady", "ramp", "spike", "sawtooth")):
        data = results[name]["rows"]
        axes[row, 0].plot([r["second"] for r in data], [r["backlog"] for r in data])
        axes[row, 1].plot(
            [r["second"] for r in data], [r["desired"] for r in data], label="desired"
        )
        axes[row, 1].plot([r["second"] for r in data], [r["ready"] for r in data], label="ready")
        axes[row, 0].set_ylabel(name + " backlog")
        axes[row, 1].legend()
    axes[-1, 0].set_xlabel("Simulated seconds")
    axes[-1, 1].set_xlabel("Simulated seconds")
    fig.suptitle("Simulated queue and replica response; not measured GPU capacity")
    fig.tight_layout()
    fig.savefig(args.output.with_suffix(".png"))
    plt.close(fig)
    structlog.get_logger().info("simulation_complete", output=str(args.output), E8=report["E8"])


if __name__ == "__main__":
    main()
