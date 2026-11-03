"""Judges a finished run against its targets."""

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from flagman_load.profiles import Targets

AGGREGATED = "Aggregated"
# One broken connection is not a verdict on the system: a smoke run has only 7 sign-ins.
_STRAY_FAILURES = 1


@dataclass(frozen=True)
class RequestStats:
    requests: int
    failures: int
    p95_ms: float | None


@dataclass(frozen=True)
class RunSummary:
    peak_rentals: int
    finished_rentals: int
    unpaid_rentals: int
    telemetry_messages: int


@dataclass(frozen=True)
class Check:
    name: str
    target: str
    actual: str
    ok: bool


def read_stats(path: Path) -> dict[str, RequestStats]:
    """Reads Locust's `*_stats.csv`; a request type that never ran has no usable percentile."""
    stats: dict[str, RequestStats] = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            raw = row["95%"]
            stats[row["Name"]] = RequestStats(
                requests=int(row["Request Count"]),
                failures=int(row["Failure Count"]),
                p95_ms=None if raw in ("", "N/A") else float(raw),
            )
    return stats


def read_exceptions(path: Path) -> int:
    """Counts what crashed inside the scenarios, from Locust's `*_exceptions.csv`."""
    with path.open(newline="", encoding="utf-8") as file:
        return sum(int(row["Count"]) for row in csv.DictReader(file))


def evaluate(
    targets: Targets, stats: Mapping[str, RequestStats], summary: RunSummary, exceptions: int
) -> list[Check]:
    checks = [_latency(name, limit, stats.get(name)) for name, limit in targets.p95_ms.items()]
    checks.append(_failures(targets.max_failure_ratio, stats))
    # A crashed scenario stops making requests, which looks like no failures at all.
    checks.append(Check("scenario crashes", "0", str(exceptions), ok=exceptions == 0))
    unpaid = summary.unpaid_rentals
    checks.append(Check("unpaid rentals", "0", str(unpaid), ok=unpaid == 0))
    checks.append(_at_least("peak rentals", targets.min_peak_rentals, summary.peak_rentals))
    checks.append(
        _at_least("finished rentals", targets.min_finished_rentals, summary.finished_rentals)
    )
    checks.append(
        _at_least("telemetry messages", targets.min_telemetry_messages, summary.telemetry_messages)
    )
    return checks


def _latency(name: str, limit: int | None, row: RequestStats | None) -> Check:
    target = "ran" if limit is None else f"p95 <= {limit} ms"
    # A scenario that silently stopped must not pass as "no slow requests".
    if row is None or row.requests == 0 or row.p95_ms is None:
        return Check(name, target, "did not run", ok=False)
    actual = f"p95 {row.p95_ms:.0f} ms, {row.requests} requests"
    return Check(name, target, actual, ok=limit is None or row.p95_ms <= limit)


def _failures(limit: float, stats: Mapping[str, RequestStats]) -> Check:
    """Per request: in a total of mostly map requests a whole dead scenario stays under 1%."""
    target = f"each < {limit:.0%}"
    rows = {name: row for name, row in stats.items() if name != AGGREGATED and row.requests}
    if not rows:
        return Check("failures", target, "no requests", ok=False)
    name, worst = max(rows.items(), key=lambda item: item[1].failures / item[1].requests)
    actual = f"worst {worst.failures} of {worst.requests}, {name}"
    return Check("failures", target, actual, ok=all(_within(r, limit) for r in rows.values()))


def _within(row: RequestStats, limit: float) -> bool:
    return row.failures <= _STRAY_FAILURES or row.failures / row.requests < limit


def _at_least(name: str, minimum: int, actual: int) -> Check:
    return Check(name, f">= {minimum}", str(actual), ok=actual >= minimum)
