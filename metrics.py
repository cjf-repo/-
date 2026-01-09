from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean, variance


def quantiles(values: list[float], qs: list[float]) -> dict[str, float]:
    if not values:
        return {f"p{int(q*100)}": 0.0 for q in qs}
    sorted_vals = sorted(values)
    out = {}
    for q in qs:
        idx = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))
        out[f"p{int(q*100)}"] = sorted_vals[idx]
    return out


def read_trace(path: Path) -> tuple[list[float], list[int]]:
    times: list[float] = []
    lengths: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            times.append(float(row["t"]))
            lengths.append(int(row["len"]))
    return times, lengths


def burst_stats(times: list[float], threshold: float = 0.05) -> dict[str, float]:
    if len(times) < 2:
        return {"burst_count": 0, "max_burst": 0, **quantiles([], [0.5, 0.9, 0.95])}
    bursts = []
    current = 1
    for i in range(1, len(times)):
        if times[i] - times[i - 1] <= threshold:
            current += 1
        else:
            bursts.append(current)
            current = 1
    bursts.append(current)
    return {
        "burst_count": len(bursts),
        "max_burst": max(bursts),
        **{f"burst_{k}": v for k, v in quantiles([float(b) for b in bursts], [0.5, 0.9, 0.95]).items()},
    }


def iat_stats(times: list[float]) -> dict[str, float]:
    if len(times) < 2:
        return {"iat_mean": 0.0, "iat_var": 0.0, **quantiles([], [0.5, 0.9, 0.95])}
    iats = [times[i] - times[i - 1] for i in range(1, len(times))]
    return {
        "iat_mean": mean(iats),
        "iat_var": variance(iats) if len(iats) > 1 else 0.0,
        **{f"iat_{k}": v for k, v in quantiles(iats, [0.5, 0.9, 0.95]).items()},
    }


def length_stats(lengths: list[int]) -> dict[str, float]:
    if not lengths:
        return {"len_mean": 0.0, "len_var": 0.0, **quantiles([], [0.5, 0.9, 0.95])}
    return {
        "len_mean": mean(lengths),
        "len_var": variance(lengths) if len(lengths) > 1 else 0.0,
        **{f"len_{k}": v for k, v in quantiles([float(v) for v in lengths], [0.5, 0.9, 0.95]).items()},
    }


def pearson(xs: list[float], ys: list[float]) -> float:
    if not xs or not ys:
        return 0.0
    n = min(len(xs), len(ys))
    xs = xs[:n]
    ys = ys[:n]
    mx = mean(xs)
    my = mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else 0.0


def summarize_trace(path: Path) -> dict[str, float]:
    times, lengths = read_trace(path)
    return {
        **length_stats(lengths),
        **iat_stats(times),
        **burst_stats(times),
    }


def summarize_attacker(run_dir: Path) -> dict[str, dict[str, float]]:
    traces_dir = run_dir / "traces"
    result: dict[str, dict[str, float]] = {}
    for tm in ("TM1", "TM2"):
        attacker_files = list(traces_dir.glob(f"trace_session_*_attacker_{tm}.csv"))
        if not attacker_files:
            result[tm] = {}
            continue
        summaries = [summarize_trace(path) for path in attacker_files]
        merged = {}
        for key in summaries[0].keys():
            merged[key] = mean([s[key] for s in summaries])
        result[tm] = merged
    return result


def path_correlation(run_dir: Path) -> dict[str, float]:
    traces_dir = run_dir / "traces"
    per_path = {}
    for path in traces_dir.glob("trace_session_*_path_*_TM1.csv"):
        times, lengths = read_trace(path)
        per_path[path.name] = lengths
    keys = list(per_path.keys())
    if len(keys) < 2:
        return {}
    corr_values = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            corr_values.append(pearson([float(v) for v in per_path[keys[i]]], [float(v) for v in per_path[keys[j]]]))
    return {
        "corr_mean": mean(corr_values) if corr_values else 0.0,
        "corr_max": max(corr_values) if corr_values else 0.0,
        "corr_min": min(corr_values) if corr_values else 0.0,
    }


def results_summary(run_dir: Path) -> dict[str, float]:
    latency_path = run_dir / "latency_logs.jsonl"
    success = 0
    total = 0
    latencies = []
    if latency_path.exists():
        for line in latency_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            total += 1
            if record.get("ok"):
                success += 1
                latencies.append(record.get("latency_ms", 0.0))
    window_path = run_dir / "window_logs.jsonl"
    total_bytes = 0.0
    real_bytes = 0.0
    padding_bytes = 0.0
    if window_path.exists():
        for line in window_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            real_bytes += float(record.get("real_bytes", 0.0))
            padding_bytes += float(record.get("padding_bytes", 0.0))
    for path in (run_dir / "traces").glob("trace_session_*_path_*_TM1.csv"):
        _, lengths = read_trace(path)
        total_bytes += sum(lengths)
    amplification = (total_bytes / real_bytes) if real_bytes > 0 else 0.0
    padding_ratio = (padding_bytes / (padding_bytes + real_bytes)) if (padding_bytes + real_bytes) > 0 else 0.0
    lat_stats = quantiles(latencies, [0.5, 0.95])
    return {
        "success_rate": (success / total) if total else 0.0,
        "latency_p50_ms": lat_stats.get("p50", 0.0),
        "latency_p95_ms": lat_stats.get("p95", 0.0),
        "bandwidth_amplification": amplification,
        "padding_ratio": padding_ratio,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    feature_summary = summarize_attacker(run_dir)
    correlation = path_correlation(run_dir)
    if correlation:
        feature_summary["path_correlation"] = correlation
    (run_dir / "feature_summary.json").write_text(
        json.dumps(feature_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = results_summary(run_dir)
    (run_dir / "results_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
