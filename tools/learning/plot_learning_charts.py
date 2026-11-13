"""Render the static charts embedded in docs/learning from measured result files.

Every chart reads an existing JSON record in docs/results; nothing is re-measured
here. Charts whose data came from the fake backend say "simulated" in the title.

    uv run python -m tools.learning.plot_learning_charts
"""

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

RESULTS = Path("docs/results")
OUT = Path("docs/learning/img")

# Reference categorical palette (slots 1-3 validate all-pairs in light mode).
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK_2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK_2,
        "axes.titlecolor": INK,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "legend.frameon": False,
        "legend.labelcolor": INK_2,
        "lines.linewidth": 2,
        "lines.markersize": 7,
        "font.size": 10,
    }
)


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RESULTS / name).read_text())
    return data


def save(figure: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(OUT / name, dpi=140)
    plt.close(figure)


def batching_sweep() -> None:
    torch_rows, fake_rows = load("e2-torch.json")["rows"], load("e2-fake.json")["rows"]
    figure, (left, right) = plt.subplots(1, 2, figsize=(10, 3.8))
    for rows, color, name in (
        (torch_rows, BLUE, "DistilBERT, CPU (real)"),
        (fake_rows, ORANGE, "Fake runner (simulated)"),
    ):
        sizes = [r["batch_size"] for r in rows]
        left.plot(sizes, [r["throughput_items_s"] for r in rows], "o-", color=color, label=name)
        right.plot(sizes, [r["amortized_item_ms"] for r in rows], "o-", color=color, label=name)
    for axis in (left, right):
        axis.set_xscale("log", base=2)
        axis.set_xticks([1, 2, 4, 8, 16, 32, 64], ["1", "2", "4", "8", "16", "32", "64"])
        axis.set_xlabel("Batch size (items per model call)")
        axis.set_ylim(bottom=0)
    left.set(title="Throughput rises with batch size", ylabel="Items per second")
    right.set(title="Cost per item falls, with diminishing returns", ylabel="Milliseconds per item")
    left.legend(loc="upper left")
    save(figure, "batching-sweep.png")


def batch_timeline() -> None:
    data = load("learning/batch-timeline.json")
    items = data["items"]
    figure, axis = plt.subplots(figsize=(10, 4.2))
    for row_index, item in enumerate(items):
        y = len(items) - row_index
        arrived = item["arrived_ms"]
        start = arrived + item["batch_wait_ms"]
        done = start + item["inference_ms"]
        axis.barh(
            y,
            item["batch_wait_ms"],
            left=arrived,
            height=0.5,
            color=GRID,
            edgecolor=MUTED,
            linewidth=0.6,
        )
        axis.barh(y, item["inference_ms"], left=start, height=0.5, color=BLUE)
        axis.plot([arrived], [y], "o", color=INK, markersize=5)
        axis.annotate(
            f"batch of {item['batch_size']}",
            (done, y),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=8,
            color=INK_2,
        )
    axis.set_yticks(range(len(items), 0, -1), [item["request"] for item in items])
    axis.axvline(0, color=MUTED, linewidth=0.8)
    axis.set(
        title=(
            "Real DynamicBatcher, max_batch_size=4, max_wait_ms=10 "
            "(fake runner: 20 ms + 2 ms/item, simulated)"
        ),
        xlabel="Milliseconds since the first arrival",
    )
    axis.grid(axis="y", visible=False)
    handles = [
        Line2D([], [], marker="o", color=INK, linestyle="", markersize=5, label="request arrives"),
        Patch(facecolor=GRID, edgecolor=MUTED, label="waiting in batcher"),
        Patch(facecolor=BLUE, label="inside model call"),
    ]
    axis.legend(handles=handles, loc="upper right", fontsize=8)
    save(figure, "batch-timeline.png")


def reclaim_recovery() -> None:
    groups: dict[int, list[float]] = defaultdict(list)
    for row in load("e4-recovery.json")["rows"]:
        groups[row["reclaim_idle_ms"]].append(row["kill_to_all_responses_ms"])
    figure, axis = plt.subplots(figsize=(7.5, 4))
    xs = sorted(groups)
    for x in xs:
        axis.plot([x] * len(groups[x]), groups[x], "o", color=BLUE, alpha=0.75)
    axis.plot([0, 2200], [0, 2200], "--", color=MUTED, linewidth=1)
    axis.annotate(
        "recovery = idle threshold",
        (1500, 1500),
        xytext=(8, -14),
        textcoords="offset points",
        color=INK_2,
        fontsize=8,
    )
    medians = [statistics.median(groups[x]) for x in xs]
    for x, value in zip(xs, medians, strict=True):
        axis.annotate(
            f"{value:.0f} ms",
            (x, value),
            xytext=(12, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=INK_2,
        )
    axis.set(
        title="kill -9 recovery = reclaim_idle_ms + one 200 ms batch (simulated)",
        xlabel="reclaim_idle_ms setting",
        ylabel="Kill to all replies (ms)",
        xlim=(0, 2600),
        ylim=(0, 2600),
    )
    save(figure, "reclaim-recovery.png")


def admission() -> None:
    rows = load("e5-overload.json")["rows"]
    figure, (left, right) = plt.subplots(1, 2, figsize=(10, 3.8))
    modes = [(True, "Admission ON"), (False, "Admission OFF")]
    codes = [
        ("200", AQUA, "200 success"),
        ("503", ORANGE, "503 shed fast"),
        ("504", BLUE, "504 timed out"),
    ]
    for index, (enabled, _) in enumerate(modes):
        trials = [r for r in rows if r["admission_enabled"] is enabled]
        left_edge = 0.0
        for code, color, name in codes:
            count = statistics.median(int(t["statuses"].get(code, 0)) for t in trials)
            if count:
                left.barh(
                    index,
                    count,
                    left=left_edge,
                    color=color,
                    height=0.55,
                    edgecolor=SURFACE,
                    linewidth=2,
                    label=None if index and code != "504" else name,
                )
                left.annotate(
                    f"{count:.0f}",
                    (left_edge + count / 2, index),
                    ha="center",
                    va="center",
                    fontsize=9,
                    color=INK,
                )
            left_edge += count
        p95 = statistics.median(t["success_p95_ms"] for t in trials)
        right.barh(index, p95, color=INK_2, height=0.55)
        right.annotate(
            f"{p95:.0f} ms",
            (p95, index),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            fontsize=9,
            color=INK_2,
        )
    for axis in (left, right):
        axis.set_yticks([0, 1], [name for _, name in modes])
        axis.invert_yaxis()
        axis.grid(axis="y", visible=False)
    left.set(title="120-request burst: what clients got", xlabel="Requests (median of 3 runs)")
    left.legend(loc="lower center", bbox_to_anchor=(0.5, -0.45), ncol=3, fontsize=8)
    right.set(title="p95 latency of the successful requests", xlabel="Milliseconds", xlim=(0, 520))
    figure.suptitle(
        "Overload with and without admission control (simulated compute)", color=INK, fontsize=11
    )
    save(figure, "admission-overload.png")


def llm_concurrency() -> None:
    by_concurrency: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in load("e6a-vllm.json")["rows"]:
        by_concurrency[row["max_concurrent"]].append(row)
    xs = sorted(by_concurrency)

    def median(field: str) -> list[float]:
        return [statistics.median(r[field] for r in by_concurrency[x]) for x in xs]

    figure, (left, right) = plt.subplots(1, 2, figsize=(10, 3.8))
    tokens = median("tokens_s")
    left.plot(xs, tokens, "o-", color=BLUE)
    for x, y in zip(xs, tokens, strict=True):
        left.annotate(
            f"{y:.0f}",
            (x, y),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=INK_2,
        )
    left.set(
        title="Engine throughput grows with concurrency",
        ylabel="Generated tokens per second",
        ylim=(0, 2100),
    )
    client, worker = median("client_ttft_p50_ms"), median("worker_ttft_p50_ms")
    right.plot(xs, client, "o-", color=ORANGE, label="client TTFT p50 (includes queueing)")
    right.plot(xs, worker, "o-", color=BLUE, label="worker TTFT p50 (dequeue to first token)")
    right.set_yscale("log")
    right.set(title="Waiting in Redis dominates time-to-first-token", ylabel="Milliseconds (log)")
    right.legend(loc="upper right", fontsize=8)
    for axis in (left, right):
        axis.set_xscale("log", base=2)
        axis.set_xticks(xs, [str(x) for x in xs])
        axis.set_xlabel("max_concurrent (adapter semaphore)")
    figure.suptitle(
        "Real vLLM on NVIDIA GB10, Qwen2.5-0.5B-Instruct, 16 requests x 32 tokens",
        color=INK,
        fontsize=11,
    )
    save(figure, "llm-concurrency.png")


def main() -> None:
    batching_sweep()
    batch_timeline()
    reclaim_recovery()
    admission()
    llm_concurrency()


if __name__ == "__main__":
    main()
