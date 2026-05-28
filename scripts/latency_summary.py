#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from pathlib import Path


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile_value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def read_events(path: Path, metric: str) -> dict[str, list[float]]:
    values_by_event: dict[str, list[float]] = defaultdict(list)
    with path.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            name = event.get("event")
            value = event.get(metric)
            if not isinstance(name, str) or not isinstance(value, int | float):
                continue
            values_by_event[name].append(float(value))
    return values_by_event


def print_summary(values_by_event: dict[str, list[float]], metric: str) -> None:
    print(f"event\tcount\tp50_{metric}\tp90_{metric}\tp95_{metric}")
    for event_name in sorted(values_by_event):
        values = values_by_event[event_name]
        print(
            "\t".join(
                [
                    event_name,
                    str(len(values)),
                    f"{percentile(values, 0.50):.3f}",
                    f"{percentile(values, 0.90):.3f}",
                    f"{percentile(values, 0.95):.3f}",
                ]
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize Orbit AI latency JSONL logs.")
    parser.add_argument("path", type=Path, help="Path to latency JSONL log.")
    parser.add_argument(
        "--metric",
        choices=("elapsed_ms", "duration_ms"),
        default="elapsed_ms",
        help="Metric to summarize. Use duration_ms for span end events.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print_summary(read_events(args.path, args.metric), args.metric)


if __name__ == "__main__":
    main()
