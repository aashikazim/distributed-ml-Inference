"""E14: speculative decoding on real vLLM, measured through gateway -> Redis -> adapter.

For each engine configuration this starts the pinned vLLM image (same flags as the
Compose ``gpu`` profile, plus an optional ``--speculative-config``), warms it up,
then runs two workloads at several adapter concurrency limits using the E6a
harness. It records the engine's speculative-decoding counters around every trial
and watches for other GPU processes so contaminated trials can be flagged.

Needs Docker with GPU access. Not run in CI.

    uv run --extra ml python -m tools.spec_decode_experiment \
        --output docs/results/e14-spec-decode.json
"""

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import structlog
from testcontainers.community.redis import RedisContainer

from tools.llm_experiments import metadata, trial

log = structlog.get_logger()

IMAGE = "vllm/vllm-openai@sha256:d825baf9ce4b1b6bfc5ecc7b79880c7e6748c65a72d697ce3d47607aafa84c6e"
CONTAINER = "infer-e14-vllm"
PORT = 8100
SMALL, LARGE = "Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct"
NGRAM = {"method": "ngram", "num_speculative_tokens": 4, "prompt_lookup_max": 4}
DRAFT = {"method": "draft_model", "model": SMALL, "num_speculative_tokens": 4}
CONFIGS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("0.5B-off", SMALL, None),
    ("0.5B-ngram", SMALL, NGRAM),
    ("1.5B-off", LARGE, None),
    ("1.5B-ngram", LARGE, NGRAM),
    ("1.5B-draft-0.5B", LARGE, DRAFT),
]
PASSAGE = (
    "Workers pull work from Redis instead of having the gateway push it to them. "
    "A busy worker simply reads less, so load balances itself and backpressure is "
    "natural. Each worker reads only as many entries as it has free capacity, so no "
    "hidden backlog builds up inside a worker process. When a worker dies, its "
    "unacknowledged entries stay pending in the consumer group, and another worker "
    "claims them after an idle threshold. The worker writes its reply before it "
    "acknowledges the entry, so a crash between the two causes a duplicate reply, "
    "never a lost one. Gateways ignore replies for requests that already finished."
)
WORKLOADS = {
    # Output overlaps the prompt heavily: the best case for prompt-lookup drafting.
    "copy-edit": (
        "Rewrite the passage below, replacing every occurrence of the word 'worker' "
        "with 'replica' and 'workers' with 'replicas'. Keep every other word exactly "
        "the same. Output only the rewritten passage.\n\n" + PASSAGE
    ),
    # Output repeats the prompt verbatim: n-gram drafting's best case.
    "verbatim-quote": (
        "Quote the passage below exactly, word for word, then add one sentence that "
        "summarizes it.\n\n" + PASSAGE
    ),
    # Output shares little with the prompt: hard to guess.
    "open-ended": (
        "Write an original short story about a lighthouse keeper who discovers that "
        "the lighthouse itself is alive. Use vivid, varied language."
    ),
}
CONCURRENCY = (1, 8, 32)
REPEATS = 3
ATTEMPTS = 3
REQUESTS = 32
TOKENS = 128
SPEC_COUNTER = re.compile(
    r"^vllm:spec_decode_num_(drafts|draft_tokens|accepted_tokens)_total\{[^}]*\} (\S+)$", re.M
)


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["docker", *args], check=check, capture_output=True, text=True, timeout=120
    ).stdout


def start_engine(model: str, spec: dict[str, Any] | None, cache: Path) -> None:
    docker("rm", "-f", CONTAINER, check=False)
    command = [
        model,
        "--max-model-len",
        "2048",
        "--max-num-seqs",
        "32",
        "--gpu-memory-utilization",
        "0.25",
        "--enforce-eager",
    ]
    if spec is not None:
        command += ["--speculative-config", json.dumps(spec)]
    docker(
        "run", "-d", "--name", CONTAINER, "--gpus", "all", "--shm-size", "2g",
        "--user", "1000:0", "-e", "HOME=/tmp/vllm", "-e", "HF_HOME=/models",
        "-v", f"{cache}:/models", "-p", f"127.0.0.1:{PORT}:8000", IMAGE, *command,
    )  # fmt: skip


async def wait_healthy(seconds: float = 1500) -> None:
    deadline = time.monotonic() + seconds
    async with httpx.AsyncClient(timeout=3) as client:
        while time.monotonic() < deadline:
            state = docker("inspect", "-f", "{{.State.Running}}", CONTAINER, check=False).strip()
            if state != "true":
                raise RuntimeError(docker("logs", "--tail", "60", CONTAINER, check=False))
            try:
                if (await client.get(f"http://127.0.0.1:{PORT}/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(3)
    raise TimeoutError("vLLM did not become healthy")


async def warm_up(model: str) -> None:
    async with httpx.AsyncClient(timeout=300) as client:
        for prompt in WORKLOADS.values():
            for _ in range(2):
                response = await client.post(
                    f"http://127.0.0.1:{PORT}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": TOKENS,
                        "temperature": 0.0,
                    },
                )
                response.raise_for_status()


async def spec_counters() -> dict[str, float]:
    async with httpx.AsyncClient(timeout=5) as client:
        text = (await client.get(f"http://127.0.0.1:{PORT}/metrics")).text
    totals: dict[str, float] = {"drafts": 0, "draft_tokens": 0, "accepted_tokens": 0}
    for match in SPEC_COUNTER.finditer(text):
        totals[match.group(1)] += float(match.group(2))
    return totals


class GpuWatcher:
    """Poll nvidia-smi; report compute processes that do not belong to the engine."""

    def __init__(self, engine_pids: set[int]) -> None:
        self.engine_pids = engine_pids
        self.foreign: dict[int, str] = {}
        self.utilization: list[int] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self) -> None:
        while not self.stop.wait(1):
            apps = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            for line in apps.splitlines():
                pid_text, _, name = line.partition(",")
                if pid_text.strip().isdigit() and int(pid_text) not in self.engine_pids:
                    self.foreign[int(pid_text)] = name.strip()
            util = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if util.isdigit():
                self.utilization.append(int(util))

    def __enter__(self) -> "GpuWatcher":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join(5)


async def wait_for_quiet_gpu(engine: set[int], quiet_s: float, timeout_s: float) -> float:
    """Block until no foreign GPU process is seen for ``quiet_s``; return seconds waited."""
    started = time.monotonic()
    quiet_since = time.monotonic()
    while True:
        apps = await asyncio.to_thread(
            subprocess.run,
            ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = {int(pid) for pid in apps.stdout.split() if pid.isdigit()}
        now = time.monotonic()
        if pids - engine:
            quiet_since = now
        elif now - quiet_since >= quiet_s:
            return now - started
        if now - started > timeout_s:
            raise TimeoutError(f"GPU not quiet for {quiet_s}s within {timeout_s}s")
        await asyncio.sleep(1)


def engine_pids() -> set[int]:
    output = docker("top", CONTAINER, "-eo", "pid", check=False)
    return {int(line) for line in output.split() if line.isdigit()}


def summarize(row: dict[str, Any]) -> dict[str, Any]:
    texts = [record["text"] for record in row.pop("records")]
    digests = Counter(hashlib.sha256(text.encode()).hexdigest()[:16] for text in texts)
    row["output_digests"] = dict(digests)
    row["sample_output"] = texts[0]
    return row


async def run(args: argparse.Namespace, url: str, root: Path) -> None:
    report: dict[str, Any] = {
        "metadata": await asyncio.to_thread(metadata, "vllm"),
        "engine": {
            "image": IMAGE,
            "flags": "--max-model-len 2048 --max-num-seqs 32 "
            "--gpu-memory-utilization 0.25 --enforce-eager",
        },
        "design": {
            "requests_per_trial": REQUESTS,
            "max_tokens": TOKENS,
            "temperature": 0.0,
            "coalesce_ms": 20,
            "repeats": REPEATS,
            "concurrency": list(CONCURRENCY),
            "prompts": WORKLOADS,
        },
        "rows": [],
    }
    selected = [c for c in CONFIGS if not args.only or c[0] in args.only]
    for name, model, spec in selected:
        started = time.monotonic()
        await asyncio.to_thread(start_engine, model, spec, args.cache)
        try:
            await wait_healthy()
            await warm_up(model)
            log.info("e14_engine_ready", config=name, startup_s=time.monotonic() - started)
            pids = await asyncio.to_thread(engine_pids)
            for workload, prompt in WORKLOADS.items():
                for concurrency in CONCURRENCY:
                    for repeat in range(1, REPEATS + 1):
                        # Another GPU job can distort a trial; retry until clean or out of tries.
                        for attempt in range(1, ATTEMPTS + 1):
                            waited = await wait_for_quiet_gpu(
                                pids, args.quiet_s, args.quiet_timeout_s
                            )
                            before = await spec_counters()
                            with GpuWatcher(pids) as watcher:
                                row = await trial(
                                    url, root, f"http://127.0.0.1:{PORT}", concurrency, 20,
                                    REQUESTS, TOKENS, prompt=prompt, served_model=model,
                                    deadline_ms=180000,
                                )  # fmt: skip
                            after = await spec_counters()
                            if not watcher.foreign:
                                break
                            log.warning(
                                "e14_contaminated", attempt=attempt, foreign=watcher.foreign
                            )
                        delta = {key: after[key] - before[key] for key in after}
                        row = summarize(row)
                        row.update(
                            config=name,
                            target_model=model,
                            speculative_config=spec,
                            workload=workload,
                            repeat=repeat,
                            spec_counters=delta,
                            acceptance_rate=(
                                delta["accepted_tokens"] / delta["draft_tokens"]
                                if delta["draft_tokens"]
                                else None
                            ),
                            mean_tokens_per_target_pass=(
                                1 + delta["accepted_tokens"] / delta["drafts"]
                                if delta["drafts"]
                                else None
                            ),
                            foreign_gpu_processes=watcher.foreign,
                            contaminated=bool(watcher.foreign),
                            attempts=attempt,
                            waited_for_quiet_gpu_s=waited,
                            gpu_utilization_samples=watcher.utilization,
                        )
                        report["rows"].append(row)
                        args.output.write_text(json.dumps(report, indent=2) + "\n")
                        log.info(
                            "e14_trial",
                            config=name,
                            workload=workload,
                            concurrency=concurrency,
                            repeat=repeat,
                            tokens_s=round(row["tokens_s"]),
                            tpot_ms=row["worker_tpot_p50_ms"],
                            acceptance=row["acceptance_rate"],
                            foreign=sorted(watcher.foreign.values()),
                        )
        finally:
            await asyncio.to_thread(docker, "rm", "-f", CONTAINER, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="E14 speculative decoding on real vLLM")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path("/tmp/infer-vllm-cache"))
    parser.add_argument("--only", nargs="*", help="subset of configuration names")
    parser.add_argument("--quiet-s", type=float, default=15, help="foreign-GPU-free seconds")
    parser.add_argument(
        "--quiet-timeout-s", type=float, default=6 * 3600, help="give up waiting after this"
    )
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="e14-") as root:
        with RedisContainer("redis:7.4") as redis:
            url = f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}/0"
            asyncio.run(run(args, url, Path(root)))


if __name__ == "__main__":
    main()
