"""Run bounded closed-loop scenarios sequentially against the dedicated kind stack."""

import csv
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import structlog


def main() -> None:
    root = Path("docs/results/locust")
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "locust": importlib.metadata.version("locust"),
        "simulated_inference": True,
        "method": (
            "Closed-loop users on real kind, 50 ms think time. Chat sends SSE but reads "
            "full body for full-duration measurement. No TTFT claim."
        ),
        "scenarios": {},
    }
    for index, name in enumerate(("steady", "ramp", "spike", "mixed")):
        if index:
            time.sleep(30)  # Deliberate idle period for the demo stabilization window.
        prefix = root / name
        command = [
            sys.executable,
            "-m",
            "locust",
            "-f",
            "tools/loadtest/locustfile.py",
            "--headless",
            "--stop-timeout",
            "35",
            "--host",
            "http://localhost:8001",
            "--csv",
            str(prefix),
            "--csv-full-history",
            "--only-summary",
        ]
        with prefix.with_suffix(".log").open("w") as output:
            completed = subprocess.run(
                command,
                env={**os.environ, "INFER_LOAD_SCENARIO": name},
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
        with Path(str(prefix) + "_stats.csv").open() as source:
            rows = list(csv.DictReader(source))
        report["scenarios"][name] = {"exit_code": completed.returncode, "stats": rows}
        Path("docs/results/p8-locust.json").write_text(json.dumps(report, indent=2) + "\n")
        structlog.get_logger().info(
            "locust_scenario", scenario=name, exit_code=completed.returncode
        )
    assert all(r["exit_code"] == 0 for r in report["scenarios"].values()), (
        "Inspect preserved Locust failures"
    )


if __name__ == "__main__":
    main()
