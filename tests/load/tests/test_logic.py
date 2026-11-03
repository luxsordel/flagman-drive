from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flagman_load import pools
from flagman_load.profiles import (
    MAP_CARS,
    PILOT,
    PROFILES,
    RENTALS_PER_CLIENT_PER_HOUR,
    Profile,
    Targets,
)
from flagman_load.run_state import ClientSession
from flagman_load.stream_clock import StreamClock
from flagman_load.thresholds import (
    AGGREGATED,
    Check,
    RequestStats,
    RunSummary,
    evaluate,
    read_exceptions,
    read_stats,
)
from flagman_load.zones import Zone, can_finish_at

T0 = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SQUARE = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
HOLE = [[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0], [4.0, 4.0]]
GOOD = {
    MAP_CARS: RequestStats(100, 0, 250.0),
    AGGREGATED: RequestStats(100, 0, 250.0),
}
ENOUGH = RunSummary(peak_rentals=2, finished_rentals=2, unpaid_rentals=0, telemetry_messages=2)


def _targets(limit: int | None = 300) -> Targets:
    return Targets(
        p95_ms={MAP_CARS: limit},
        min_peak_rentals=2,
        min_finished_rentals=2,
        min_telemetry_messages=2,
    )


def _failed(checks: list[Check]) -> set[str]:
    return {check.name for check in checks if not check.ok}


def test_a_run_within_targets_passes() -> None:
    assert _failed(evaluate(_targets(), GOOD, ENOUGH, 0)) == set()


def test_slow_requests_fail_above_the_target_not_at_it() -> None:
    at_limit = {**GOOD, MAP_CARS: RequestStats(100, 0, 300.0)}
    over_limit = {**GOOD, MAP_CARS: RequestStats(100, 0, 310.0)}
    assert _failed(evaluate(_targets(), at_limit, ENOUGH, 0)) == set()
    assert _failed(evaluate(_targets(), over_limit, ENOUGH, 0)) == {MAP_CARS}


@pytest.mark.parametrize(
    "stats",
    [{AGGREGATED: GOOD[AGGREGATED]}, {**GOOD, MAP_CARS: RequestStats(0, 0, None)}],
)
def test_a_scenario_that_did_not_run_fails_even_without_a_latency_target(
    stats: dict[str, RequestStats],
) -> None:
    assert MAP_CARS in _failed(evaluate(_targets(limit=None), stats, ENOUGH, 0))


def test_a_dead_scenario_fails_even_if_the_total_stays_under_the_limit() -> None:
    stats = {
        **GOOD,
        MAP_CARS: RequestStats(9000, 0, 250.0),
        "POST /auth/otp/verify": RequestStats(90, 90, 10.0),
        AGGREGATED: RequestStats(9090, 90, 250.0),
    }
    assert _failed(evaluate(_targets(), stats, ENOUGH, 0)) == {"failures"}


def test_one_stray_failure_in_a_small_sample_passes_and_two_do_not() -> None:
    one = {**GOOD, "POST /auth/otp/verify": RequestStats(7, 1, 10.0)}
    two = {**GOOD, "POST /auth/otp/verify": RequestStats(7, 2, 10.0)}
    assert _failed(evaluate(_targets(), one, ENOUGH, 0)) == set()
    assert _failed(evaluate(_targets(), two, ENOUGH, 0)) == {"failures"}


def test_failures_at_the_ratio_fail_but_below_it_pass() -> None:
    at_limit = {**GOOD, MAP_CARS: RequestStats(200, 2, 250.0)}
    below = {**GOOD, MAP_CARS: RequestStats(300, 2, 250.0)}
    assert _failed(evaluate(_targets(), at_limit, ENOUGH, 0)) == {"failures"}
    assert _failed(evaluate(_targets(), below, ENOUGH, 0)) == set()


def test_a_crash_inside_a_scenario_fails_the_run() -> None:
    assert _failed(evaluate(_targets(), GOOD, ENOUGH, 1)) == {"scenario crashes"}


def test_an_unpaid_rental_fails_the_run() -> None:
    unpaid = RunSummary(peak_rentals=2, finished_rentals=2, unpaid_rentals=1, telemetry_messages=2)
    assert _failed(evaluate(_targets(), GOOD, unpaid, 0)) == {"unpaid rentals"}


def test_read_exceptions_sums_the_counts(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.csv"
    path.write_text('Count,Message,Traceback,Nodes\n2,a,"t",n\n3,b,"t",n\n', encoding="utf-8")
    assert read_exceptions(path) == 5


def test_too_few_rentals_or_telemetry_fail() -> None:
    assert _failed(evaluate(_targets(), GOOD, RunSummary(1, 1, 0, 1), 0)) == {
        "peak rentals",
        "finished rentals",
        "telemetry messages",
    }


def test_read_stats_understands_locust_csv(tmp_path: Path) -> None:
    path = tmp_path / "stats.csv"
    path.write_text(
        '"Type","Name","Request Count","Failure Count","95%"\n'
        "GET,GET /map/cars,10,1,120\n"
        "GET,GET /map/zones,0,0,N/A\n"
        ",Aggregated,10,1,120\n",
        encoding="utf-8",
    )
    stats = read_stats(path)
    assert stats[MAP_CARS] == RequestStats(10, 1, 120.0)
    assert stats["GET /map/zones"].p95_ms is None


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [(1.0, 1.0, True), (20.0, 1.0, False), (5.0, 5.0, False)],
)
def test_zone_with_a_hole(lat: float, lon: float, expected: bool) -> None:
    assert can_finish_at([Zone("allowed", [SQUARE, HOLE])], lat, lon) is expected


def test_forbidden_zone_wins_over_allowed() -> None:
    zones = [Zone("allowed", [SQUARE]), Zone("forbidden", [HOLE])]
    assert can_finish_at(zones, 5.0, 5.0) is False
    assert can_finish_at(zones, 1.0, 1.0) is True


def test_no_zones_means_no_finish() -> None:
    assert can_finish_at([], 1.0, 1.0) is False


def test_lag_ignores_the_clock_difference() -> None:
    clock = StreamClock()
    skew = timedelta(seconds=7)
    received = T0 + skew + timedelta(milliseconds=200)
    clock.observe(received, T0)
    # The car reported 500 ms before the server sent the message.
    assert clock.lag_ms(received, [T0 - timedelta(milliseconds=500)]) == pytest.approx(500)


def test_lag_shows_a_backlog_on_the_socket() -> None:
    clock = StreamClock()
    clock.observe(T0 + timedelta(milliseconds=10), T0)
    late_sent = T0 + timedelta(seconds=5)
    late_received = late_sent + timedelta(seconds=3)
    clock.observe(late_received, late_sent)
    assert clock.lag_ms(late_received, [late_sent]) == pytest.approx(2990)


def test_repeated_telemetry_is_not_a_delay() -> None:
    clock = StreamClock()
    clock.observe(T0, T0)
    assert clock.lag_ms(T0, [T0]) is not None
    # A reservation announces the car again, with the same telemetry, seconds later.
    assert clock.lag_ms(T0 + timedelta(seconds=4), [T0]) is None
    assert clock.lag_ms(T0 + timedelta(seconds=4), []) is None


def test_guessable_codes_the_api_refuses_are_recognised() -> None:
    assert pools.is_guessable_code("111111")
    assert pools.is_guessable_code("123456")
    assert pools.is_guessable_code("654321")
    assert not pools.is_guessable_code("402918")


def test_phones_are_unique_and_match_the_pilot_format() -> None:
    generated = pools.phones(PILOT.phones_needed)
    assert len(set(generated)) == PILOT.phones_needed
    assert all(len(phone) == 13 and phone.startswith("+37529") for phone in generated)


def test_session_is_refreshed_before_the_token_expires() -> None:
    session = ClientSession("10.0.0.1", "a", "r", issued_at=0.0)
    assert not session.needs_refresh(599.0)
    assert session.needs_refresh(601.0)


@pytest.mark.parametrize("profile", PROFILES.values(), ids=lambda p: p.name)
def test_a_run_cannot_outgrow_the_upload_limit_of_its_clients(profile: Profile) -> None:
    assert profile.most_rentals <= profile.renter_pool * RENTALS_PER_CLIENT_PER_HOUR
