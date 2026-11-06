"""E2: runner-only benchmark. Batch latency is distinct from amortized item cost."""

import argparse
import json
import statistics
import time
from pathlib import Path

import structlog

from tools.bench.shared import TEXT, metadata, runner_for


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["fake", "torch"], required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or args.threads < 1:
        parser.error("repeats and threads must be positive")
    runner = runner_for(args.backend, args.threads)
    runner.load()
    runner.warmup()
    rows = []
    for size in (1, 2, 4, 8, 16, 32, 64):
        inputs = [{"input": TEXT} for _ in range(size)]
        for _ in range(2):
            runner.predict_batch(inputs)
        times = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            outputs = runner.predict_batch(inputs)
            times.append(time.perf_counter() - start)
            assert len(outputs) == size
        rows.append(
            {
                "batch_size": size,
                "samples_s": times,
                "throughput_items_s": size * args.repeats / sum(times),
                "batch_latency_ms": statistics.mean(times) * 1000,
                "amortized_item_ms": statistics.mean(times) * 1000 / size,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "experiment": "E2",
                "backend": args.backend,
                "simulated": args.backend == "fake",
                "threads": args.threads,
                "repeats": args.repeats,
                "metadata": metadata(runner),
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )
    structlog.get_logger().info("E2_complete", backend=args.backend, rows=rows)


if __name__ == "__main__":
    main()
