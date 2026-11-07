"""Prepare only this project's disposable ARM64 kind node for real CDI GPU testing.

The host Docker daemon is unchanged. Driver mounts are copied into the node;
this is a local verification helper, not a production driver installer.
"""

import os
import stat
import subprocess
import tempfile
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()
NODE = "infer-platform-control-plane"


def run(*args: str) -> None:
    subprocess.run(args, check=True, timeout=120)


def main() -> None:
    label = subprocess.check_output(
        ["docker", "inspect", NODE, "--format", '{{index .Config.Labels "io.x-k8s.kind.cluster"}}'],
        text=True,
        timeout=10,
    ).strip()
    if label != "infer-platform" or os.uname().machine != "aarch64":
        raise RuntimeError(
            "This verified helper targets only the dedicated ARM64 infer-platform node"
        )
    with tempfile.TemporaryDirectory(prefix="infer-kind-gpu-") as temp:
        spec = Path(temp) / "nvidia.yaml"
        run("nvidia-ctk", "cdi", "generate", f"--output={spec}")
        data = yaml.safe_load(spec.read_text())
        mounts = [m for m in data["containerEdits"]["mounts"] if os.path.isfile(m["hostPath"])]
        data["containerEdits"]["mounts"] = mounts
        paths = {m["hostPath"] for m in mounts} | {
            "/usr/bin/nvidia-ctk",
            "/usr/bin/nvidia-cdi-hook",
        }
        for path in sorted(paths):
            run("docker", "exec", NODE, "mkdir", "-p", str(Path(path).parent))
            run("docker", "cp", "-L", path, f"{NODE}:{path}")
        devices = (
            data["containerEdits"]["deviceNodes"]
            + data["devices"][0]["containerEdits"]["deviceNodes"]
        )
        for device in devices:
            path = device["path"]
            info = os.stat(path)
            if not stat.S_ISCHR(info.st_mode):
                raise RuntimeError(f"Expected character device: {path}")
            run("docker", "exec", NODE, "mkdir", "-p", str(Path(path).parent))
            exists = subprocess.run(["docker", "exec", NODE, "test", "-e", path], timeout=10)
            if exists.returncode:
                run(
                    "docker",
                    "exec",
                    NODE,
                    "mknod",
                    "-m",
                    "666",
                    path,
                    "c",
                    str(os.major(info.st_rdev)),
                    str(os.minor(info.st_rdev)),
                )
        spec.write_text(yaml.safe_dump(data))
        run("docker", "exec", NODE, "mkdir", "-p", "/etc/cdi")
        run("docker", "cp", str(spec), f"{NODE}:/etc/cdi/nvidia.yaml")
        run("docker", "exec", NODE, "ldconfig")
    run(
        "kubectl",
        "--context",
        "kind-infer-platform",
        "apply",
        "-f",
        "deploy/k8s/kind-gpu/device-plugin.yaml",
    )
    run(
        "kubectl",
        "--context",
        "kind-infer-platform",
        "label",
        "node",
        NODE,
        "nvidia.com/gpu.present=true",
        "--overwrite",
    )
    log.info("kind_gpu_prepared", node=NODE, driver_files=len(paths))


if __name__ == "__main__":
    main()
