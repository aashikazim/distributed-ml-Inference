"""Derive a scoped local proposal from actual benchmark trials, never a production promise."""

import json
import math
from pathlib import Path

import yaml

from infer_platform.common.metrics import LATENCY_BUCKETS


def main() -> None:
    data = json.loads(Path("docs/results/p8-e2e.json").read_text())
    models = {}
    for model in ("fake-classifier", "sentiment"):
        trials = [t for t in data["trials"] if t["model"] == model]
        count = sum(sum(t["statuses"].values()) for t in trials)
        failures = sum(sum(v for k, v in t["statuses"].items() if k != "200") for t in trials)
        worst = max(t["p95_s"] for t in trials)
        target = next(bucket for bucket in LATENCY_BUCKETS if bucket >= 2 * worst)
        models[model] = {
            "observations": count,
            "failures": failures,
            "worst_trial_raw_p95_s": worst,
            "latency_target_s": target,
            "measured_max_offered_rps": max(t["offered_rps"] for t in trials),
            "simulated_compute": model == "fake-classifier",
        }
    least = min(v["observations"] for v in models.values())
    budget = math.ceil(100 * 3 / least) / 100
    proposal = {
        "status": "local candidate, not production SLO verification",
        "source_commit": data["commit"].strip(),
        "method": (
            "Double worst per-model trial p95, round up to shared histogram boundary. "
            "Failure budget rounds rough 3/N independent-trial estimate up to a percentage; "
            "correlated short runs do not establish month-long reliability."
        ),
        "window_proposal_days": 30,
        "availability_target": 1 - budget,
        "failure_budget_fraction": budget,
        "scope": (
            "kind fixed 2 fake workers / 1 CPU sentiment worker / 2 gateways, short text; "
            "tested offered rates only; 429/5xx count as failures for valid submissions. "
            "No chat SLO derived from fake token output."
        ),
        "models": models,
    }
    Path("docs/results/p8-slo-proposal.json").write_text(json.dumps(proposal, indent=2) + "\n")
    path = Path("deploy/monitoring/alerts.yml")
    rules = yaml.safe_load(path.read_text())
    rules["groups"] = [g for g in rules["groups"] if g["name"] != "measured-local-targets"]
    alerts = rules["groups"][0]["rules"]
    alerts[:] = [r for r in alerts if r["alert"] != "InferenceFailureBudgetExceeded"]
    alerts.append(
        {
            "alert": "InferenceFailureBudgetExceeded",
            "expr": (
                "sum by (model) (rate(gateway_requests_total{"
                'endpoint="/v1/infer/{model}",status=~"429|5.."}[5m])) / '
                "sum by (model) (rate(gateway_requests_total{"
                'endpoint="/v1/infer/{model}",status=~"2..|429|5.."}[5m])) > ' + str(budget)
            ),
            "for": "5m",
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "Valid inference failure fraction exceeds the local candidate budget"
            },
        }
    )
    rules["groups"].append(
        {
            "name": "measured-local-targets",
            "rules": [
                {
                    "record": "platform_latency_slo_seconds",
                    "expr": str(v["latency_target_s"]),
                    "labels": {"model": name},
                }
                for name, v in models.items()
            ],
        }
    )
    rendered = yaml.safe_dump(rules, sort_keys=False)
    path.write_text(rendered)
    Path("deploy/k8s/base/monitoring/alerts.yml").write_text(rendered)


if __name__ == "__main__":
    main()
