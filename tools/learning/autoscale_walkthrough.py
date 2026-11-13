"""Step the real autoscaler decide() through a scripted spike, printing every decision.

Used by docs/learning/06-autoscaling.md. Pure function, no Redis or Kubernetes:
replicas are assumed to become ready instantly, which real clusters do not do.

    uv run python -m tools.learning.autoscale_walkthrough
"""

import sys
from math import ceil

from infer_platform.autoscaler.policy import History, Policy, State, decide

# fake-classifier policy from config/models.yaml; other fields keep their defaults.
POLICY = Policy(min=1, max=10, target_backlog_per_replica=20, target_busy_ratio=0.7)

# (seconds, backlog, mean busy ratio, head age seconds)
SCRIPT = [
    (0, 0, 0.30, 0.0),
    (5, 150, 1.00, 0.8),
    (10, 140, 1.00, 0.9),
    (15, 60, 0.95, 0.6),
    (20, 10, 0.80, 0.1),
    (25, 0, 0.40, 0.0),
    (60, 0, 0.10, 0.0),
    (120, 0, 0.10, 0.0),
    (145, 0, 0.10, 0.0),
    (150, 0, 0.10, 0.0),
    (185, 0, 0.10, 0.0),
    (270, 0, 0.10, 0.0),
]


def main() -> None:
    history, current = History(), 1
    header = "| t (s) | backlog | busy | head age | current | by_backlog | by_util | by_latency |"
    sys.stdout.write(header + " desired | reason |\n")
    sys.stdout.write("|" + "---|" * 10 + "\n")
    for now, backlog, busy, head_age in SCRIPT:
        state = State(current, backlog, busy, head_age, workers_alive=current)
        decision = decide(state, POLICY, history, float(now))
        # Same float arithmetic as decide(), so the table shows what the code computes.
        by_backlog = ceil(backlog / POLICY.target_backlog_per_replica)
        by_util = ceil(current * busy / POLICY.target_busy_ratio)
        by_latency = current + 1 if head_age > POLICY.max_head_age_s else 0
        sys.stdout.write(
            f"| {now} | {backlog} | {busy:.2f} | {head_age:.1f} | {current} | {by_backlog} "
            f"| {by_util} | {by_latency} | **{decision.desired}** | {decision.reason} |\n"
        )
        history, current = decision.history, decision.desired


if __name__ == "__main__":
    main()
