import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main() -> None:
    data = json.loads(Path("docs/results/p8-e2e.json").read_text())
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    rates = (5, 20, 40)
    groups = [
        [r for r in data["trials"] if r["model"] == "fake-classifier" and r["offered_rps"] == rate]
        for rate in rates
    ]
    p95 = [statistics.median(r["p95_s"] for r in rows) * 1000 for rows in groups]
    low = [v - min(r["p95_s"] for r in rows) * 1000 for v, rows in zip(p95, groups, strict=True)]
    high = [max(r["p95_s"] for r in rows) * 1000 - v for v, rows in zip(p95, groups, strict=True)]
    axes[0].errorbar(rates, p95, yerr=[low, high], marker="o", label="Fake compute, measured HTTP")
    cpu = [r for r in data["trials"] if r["model"] == "sentiment"]
    axes[0].scatter(
        [5],
        [statistics.median(r["p95_s"] for r in cpu) * 1000],
        label="Real CPU DistilBERT",
        marker="s",
    )
    axes[0].set(xlabel="Offered requests/s", ylabel="Raw HTTP p95 milliseconds")
    axes[0].legend()
    axes[1].plot(
        rates, [statistics.median(r["completed_rps"] for r in rows) for rows in groups], marker="o"
    )
    axes[1].set(xlabel="Offered requests/s", ylabel="Completed requests/s")
    figure.suptitle("Local kind: fixed 2 fake workers / 1 CPU worker; 3 short trials")
    figure.tight_layout()
    figure.savefig("docs/results/p8-latency.png")
    plt.close(figure)
    sample = data["capacity_measurement"]
    points = [(t, n) for t, n in sample["inflight_samples"] if t < 5]
    figure, axis = plt.subplots(figsize=(10, 3))
    axis.step(
        [t for t, n in points],
        [n for t, n in points],
        where="post",
        label="Independent in-flight samples",
    )
    axis.axhline(0.67, label="Predicted mean 0.67", linestyle="--", color="orange")
    axis.axhline(
        sample["sampled_mean_inflight"],
        label=f"Measured mean {sample['sampled_mean_inflight']:.3f}",
        linestyle=":",
        color="green",
    )
    axis.set(
        xlabel="Seconds (first five shown)",
        ylabel="Active HTTP requests",
        title="Little's Law: fractional average, whole individual requests",
    )
    axis.legend(loc="lower right")
    figure.tight_layout()
    figure.savefig("docs/results/p8-little-law.png")
    plt.close(figure)


if __name__ == "__main__":
    main()
