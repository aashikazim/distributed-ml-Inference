from pathlib import Path

import yaml


def test_deployment_drain_resources_and_narrow_rbac():
    root = Path("deploy/k8s/base")
    objects = list(yaml.safe_load_all((root / "workloads.yaml").read_text()))
    for deployment in (o for o in objects if o["kind"] == "Deployment"):
        spec = deployment["spec"]["template"]["spec"]
        assert spec["securityContext"]["runAsNonRoot"]
        for container in spec["containers"]:
            assert container["resources"]["requests"]
            assert container["startupProbe"]
            assert container["readinessProbe"]
            assert container["livenessProbe"]
        if deployment["metadata"]["name"].startswith("worker"):
            assert spec["terminationGracePeriodSeconds"] >= 45
    role = next(
        o for o in yaml.safe_load_all((root / "rbac.yaml").read_text()) if o["kind"] == "Role"
    )
    assert role["rules"][0]["resources"] == ["deployments/scale"]
    assert role["rules"][0]["verbs"] == ["get", "patch"]
    # Monitoring copies must stay aligned when later phases tune measured alerts.
    assert (root / "monitoring/alerts.yml").read_text() == Path(
        "deploy/monitoring/alerts.yml"
    ).read_text()
    for name in ("overview", "batching", "llm", "autoscaler"):
        assert (root / f"monitoring/{name}.json").read_text() == Path(
            f"deploy/monitoring/grafana/dashboards/{name}.json"
        ).read_text()
