"""What each run loads the system with and what it has to reach (docs/09-load-test.md)."""

from collections.abc import Mapping
from dataclasses import dataclass

MAP_CARS = "GET /map/cars"
MAP_ZONES = "GET /map/zones"
OTP_REQUEST = "POST /auth/otp/request"
OTP_VERIFY = "POST /auth/otp/verify"
RESERVE = "POST /rentals"
OPEN = "POST /rentals/{id}/commands open"
FINISH = "POST /rentals/{id}/finish"
PHOTO = "POST /rentals/{id}/inspections/{stage}/photos"
STREAM_SNAPSHOT = "WS /admin/fleet/stream snapshot"
TELEMETRY_LAG = "WS telemetry lag"

_ALL_REQUESTS = (
    MAP_CARS,
    MAP_ZONES,
    OTP_REQUEST,
    OTP_VERIFY,
    RESERVE,
    OPEN,
    FINISH,
    PHOTO,
    STREAM_SNAPSHOT,
    TELEMETRY_LAG,
)

_PILOT_P95_MS: Mapping[str, int | None] = {
    MAP_CARS: 300,
    MAP_ZONES: 300,
    OTP_REQUEST: 500,
    OTP_VERIFY: 500,
    RESERVE: 1000,
    OPEN: 1000,
    FINISH: 1000,
    PHOTO: 1500,
    STREAM_SNAPSHOT: None,
    TELEMETRY_LAG: 2000,
}

MAX_FAILURE_RATIO = 0.01
# The API allows 40 inspection uploads an hour per client and a rental takes 8 photos.
RENTALS_PER_CLIENT_PER_HOUR = 5
INSPECTION_PHOTOS = 4
# Besides the trip itself: the photos and the other requests of one rental, at the least.
MIN_RENTAL_OVERHEAD_S = 2.0
# The stack is about to be removed, so rentals may be cut off. Waiting for them would add finishes
# at no load, and those would flatter the percentile.
_LOCAL_STOP_TIMEOUT_S = 15


@dataclass(frozen=True)
class Targets:
    # None: the request has to run, its latency is not judged.
    p95_ms: Mapping[str, int | None]
    min_peak_rentals: int
    min_finished_rentals: int
    min_telemetry_messages: int
    max_failure_ratio: float = MAX_FAILURE_RATIO


@dataclass(frozen=True)
class Profile:
    name: str
    renters: int
    renter_pool: int
    map_users: int
    otp_users: int
    otp_phones: int
    staff_streams: int
    run_time_s: int
    spawn_rate: int
    trip_s: tuple[float, float]
    map_wait_s: tuple[float, float]
    otp_wait_s: tuple[float, float]
    # How long rentals under way may finish once the run ends, then Locust cuts them off.
    stop_timeout_s: int
    targets: Targets

    @property
    def users(self) -> int:
        return self.renters + self.map_users + self.otp_users + self.staff_streams

    @property
    def phones_needed(self) -> int:
        return self.renter_pool + self.map_users + self.otp_phones

    @property
    def most_rentals(self) -> float:
        """The rentals the run can start at most, to compare with what the pool may do."""
        return self.renters * self.run_time_s / (self.trip_s[0] + MIN_RENTAL_OVERHEAD_S)


# Checks that every scenario still runs. CI runners are too noisy to judge latency on.
SMOKE = Profile(
    name="smoke",
    renters=2,
    renter_pool=4,
    map_users=3,
    otp_users=1,
    otp_phones=8,
    staff_streams=1,
    run_time_s=75,
    spawn_rate=5,
    trip_s=(8, 12),
    map_wait_s=(3, 6),
    otp_wait_s=(10, 15),
    stop_timeout_s=_LOCAL_STOP_TIMEOUT_S,
    targets=Targets(
        p95_ms=dict.fromkeys(_ALL_REQUESTS),
        min_peak_rentals=1,
        min_finished_rentals=1,
        min_telemetry_messages=3,
    ),
)

# The whole pilot fleet rented at once, on a local stack.
PILOT = Profile(
    name="pilot",
    renters=30,
    renter_pool=60,
    map_users=200,
    otp_users=2,
    otp_phones=60,
    staff_streams=5,
    run_time_s=600,
    spawn_rate=5,
    trip_s=(60, 120),
    map_wait_s=(5, 15),
    otp_wait_s=(5, 10),
    stop_timeout_s=_LOCAL_STOP_TIMEOUT_S,
    targets=Targets(
        p95_ms=_PILOT_P95_MS,
        min_peak_rentals=30,
        min_finished_rentals=60,
        min_telemetry_messages=100,
    ),
)

# Small on purpose: the stand is shown to management, has 2 vCPU and one IP limit for all sign-ins
# (20 codes an hour), so this pool is about all one run may sign in.
STAND = Profile(
    name="stand",
    renters=2,
    renter_pool=5,
    map_users=3,
    otp_users=1,
    otp_phones=2,
    staff_streams=2,
    run_time_s=300,
    spawn_rate=1,
    trip_s=(30, 50),
    map_wait_s=(5, 15),
    otp_wait_s=(180, 240),
    # The longest trip, its photos and the retries of a finish: an open rental would stay on the
    # stand, billed, and block its client in the next run.
    stop_timeout_s=120,
    targets=Targets(
        p95_ms=_PILOT_P95_MS,
        min_peak_rentals=1,
        min_finished_rentals=3,
        min_telemetry_messages=20,
    ),
)

PROFILES: Mapping[str, Profile] = {p.name: p for p in (SMOKE, PILOT, STAND)}

# Cars of the local emulator; the stand's fleet is set on the server. More than the rentals: with
# EMULATOR_SEED=42 only 23 of 30 and 32 of 50 cars stand in the zone where a rental can end.
LOCAL_CAR_COUNTS: Mapping[str, int] = {SMOKE.name: 30, PILOT.name: 50}
