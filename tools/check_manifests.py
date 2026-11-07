"""Validate the rendered deployment boundary, including overlay-added resources."""

import sys

import structlog
import yaml

log = structlog.get_logger()


def main() -> None:
    objects = list(yaml.safe_load_all(sys.stdin))
    cluster_kinds = {"Namespace", "ClusterRole", "ClusterRoleBinding", "PersistentVolume"}
    for item in objects:
        if item["kind"] not in cluster_kinds:
            namespace = item["metadata"].get("namespace")
            if namespace != "infer-platform":
                raise ValueError(
                    f"{item['kind']}/{item['metadata']['name']} rendered into {namespace!r}; "
                    "all application resources must share infer-platform"
                )
        if item["kind"] == "Deployment" and item["metadata"]["name"] == "vllm":
            if item["spec"]["template"]["spec"].get("enableServiceLinks", True):
                raise ValueError("vLLM service environment injection conflicts with VLLM_PORT")
    log.info("rendered_manifests_verified", objects=len(objects))


if __name__ == "__main__":
    main()
