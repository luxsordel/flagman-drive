"""Judges a finished run against its targets."""

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from flagman_load.profiles import Targets

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


class LocustEntry(Protocol):
    """Locust's `StatsEntry`. Not imported: Locust patches the importing process with gevent."""

    name: str
    num_requests: int
    num_failures: int

    def get_response_time_percentile(self, percent: float) -> float | None: ...


def request_stats(entries: Iterable[LocustEntry]) -> dict[str, RequestStats]:
    return {
        entry.name: RequestStats(
            requests=entry.num_requests,
            failures=entry.num_failures,
            p95_ms=(
                float(entry.get_response_time_percentile(0.95) or 0) if entry.num_requests else None
            ),
        )
        for entry in entries
    }


@dataclass(frozen=True)
class RunResult:
    """Taken as Locust quits: its CSV files lag by up to a second and miss the end of the run."""

    summary: RunSummary
    requests: dict[str, RequestStats]
    crashes: int

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self)), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RunResult":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            summary=RunSummary(**raw["summary"]),
            requests={name: RequestStats(**row) for name, row in raw["requests"].items()},
            crashes=raw["crashes"],
        )


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
    rows = {name: row for name, row in stats.items() if row.requests}
    if not rows:
        return Check("failures", target, "no requests", ok=False)
    name, worst = max(rows.items(), key=lambda item: item[1].failures / item[1].requests)
    actual = f"worst {worst.failures} of {worst.requests}, {name}"
    return Check("failures", target, actual, ok=all(_within(r, limit) for r in rows.values()))


def _within(row: RequestStats, limit: float) -> bool:
    return row.failures <= _STRAY_FAILURES or row.failures / row.requests < limit


def _at_least(name: str, minimum: int, actual: int) -> Check:
    return Check(name, f">= {minimum}", str(actual), ok=actual >= minimum)
