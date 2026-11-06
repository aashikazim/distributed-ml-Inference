import json
from pathlib import Path

import yaml
from prometheus_client import generate_latest

from infer_platform.common.metrics import AutoscalerMetrics


def test_dashboards_use_provisioned_datasource_and_have_queries():
    root = Path("deploy/monitoring")
    for path in (root / "grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        assert dashboard["panels"]
        for panel in dashboard["panels"]:
            assert panel["datasource"]["uid"] == "prometheus"
            assert panel["targets"][0]["expr"]
    rules = yaml.safe_load((root / "alerts.yml").read_text())["groups"][0]["rules"]
    assert any(r["alert"] == "InferenceLatencyAboveMeasuredSLO" and r["for"] == "5m" for r in rules)
    metrics = AutoscalerMetrics()
    metrics.desired.labels("fake-classifier").set(2)
    metrics.current.labels("fake-classifier").set(1)
    metrics.decisions.labels("fake-classifier", "up").inc()
    text = generate_latest(metrics.registry).decode()
    for name in (
        "autoscaler_desired_replicas",
        "autoscaler_current_replicas",
        "autoscaler_decisions_total",
    ):
        assert name in text
