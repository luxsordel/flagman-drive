"""Public interface of the fleet for other modules.

Reads join the caller's transaction if one is open; changes run only inside it, so a rental
(stage 3) changes the car status and its own rows in one transaction. The demo seed, the
telemetry intake and manual car commands are the exceptions: each is a command of its own and
opens its own transaction.
"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    RequestOrigin,
)
from flagman.core.config import Settings
from flagman.core.errors import AppError
from flagman.fleet import domain, repository
from flagman.fleet.changes import CommandNotice, encode_car_changes, encode_command
from flagman.fleet.domain import (
    AvailabilityRules,
    Car,
    CarCommand,
    CarCommandDuplicateError,
    CarCommandRejectedError,
    CarInActiveRentalError,
    CarNotFoundError,
    CarOnMap,
    CarState,
    CarStatus,
    CarTransition,
    CommandOutcome,
    MapQuery,
    SnapshotFromFutureError,
    SnapshotOutcome,
    StaffCarFilter,
    StoredCarModel,
    TelematicsTimeoutError,
    TelematicsUnavailableError,
    TelemetryReport,
    TelemetrySnapshot,
    apply_transition,
    car_on_map,
    check_snapshot_time,
    emulator_car_id,
    normalize_plate,
    normalize_vin,
)
from flagman.fleet.emulator import EmulatorTelematicsGateway
from flagman.fleet.ports import TelematicsGateway
from flagman.fleet.seed import DEMO_MODELS, MAX_DEMO_CARS, demo_plate, demo_vin

logger = structlog.get_logger(__name__)

# Enough to find the cars in the emulator or the seed; a wrong seed would list every car.
_LOGGED_UNKNOWN_CARS = 10


def create_telematics_gateway(settings: Settings, client: httpx2.AsyncClient) -> TelematicsGateway:
    """Raises TelematicsUnavailableError when car commands are not configured."""
    if settings.emulator_url is None or settings.emulator_command_secret is None:
        raise TelematicsUnavailableError("Car commands are not configured.")
    return EmulatorTelematicsGateway(
        client=client,
        base_url=settings.emulator_url,
        secret=settings.emulator_command_secret.get_secret_value().encode(),
        timeout_s=settings.telematics_command_timeout_seconds,
    )


def telemetry_clock_skew(settings: Settings) -> timedelta:
    return timedelta(seconds=settings.telematics_signature_tolerance_seconds)


def availability_rules(settings: Settings) -> AvailabilityRules:
    return AvailabilityRules(
        min_battery_pct=settings.fleet_min_battery_pct,
        max_state_age=timedelta(seconds=settings.fleet_telemetry_max_age_seconds),
    )


async def _read[T](db: AsyncSession, read: Callable[[], Awaitable[T]]) -> T:
    if db.in_transaction():
        return await read()
    async with db.begin():
        return await read()


def is_rentable(car: Car, rules: AvailabilityRules, now: datetime) -> bool:
    """Whether the car may be offered to a client now; the rule is in fleet.domain."""
    return domain.is_rentable(car.status, car.state, rules, now)


async def find_cars_on_map(
    db: AsyncSession, rules: AvailabilityRules, now: datetime, query: MapQuery
) -> list[CarOnMap]:
    """Rentable cars for the client map; `query.near` is used for this query only, never kept."""
    if query.area is None and query.near is None:
        raise ValueError("give an area, a point or both: the whole fleet is not a map")
    rows = await _read(db, lambda: repository.find_rentable_cars(db, rules, now, query))
    return [
        car_on_map(car, distance)
        for car, distance in rows
        if domain.is_rentable(car.status, car.state, rules, now) and _of_models(car, query)
    ]


def _of_models(car: Car, query: MapQuery) -> bool:
    return query.models is None or car.model_id in query.models


async def get_car_on_map(
    db: AsyncSession, car_id: UUID, rules: AvailabilityRules, now: datetime, query: MapQuery
) -> CarOnMap:
    """Raises CarNotFoundError for a car that is not rentable now as well as for a missing one.

    Only `query.near` and `query.models` matter: the car is looked up by its id.
    """
    query = replace(query, area=None, limit=1)
    rows = await _read(db, lambda: repository.find_rentable_cars(db, rules, now, query, car_id))
    for car, distance in rows:
        if domain.is_rentable(car.status, car.state, rules, now) and _of_models(car, query):
            return car_on_map(car, distance)
    raise CarNotFoundError


async def get_car(db: AsyncSession, car_id: UUID) -> Car:
    if db.in_transaction():
        car = await repository.get_car(db, car_id)
    else:
        async with db.begin():
            car = await repository.get_car(db, car_id)
    if car is None:
        raise CarNotFoundError
    return car


async def list_cars(db: AsyncSession) -> list[Car]:
    if db.in_transaction():
        return await repository.list_cars(db)
    async with db.begin():
        return await repository.list_cars(db)


async def get_cars(db: AsyncSession, car_ids: Collection[UUID]) -> list[Car]:
    """The cars of these ids that exist, by plate."""
    return await _read(db, lambda: repository.get_cars(db, car_ids))


async def list_cars_for_staff(
    db: AsyncSession, rules: AvailabilityRules, car_filter: StaffCarFilter
) -> list[Car]:
    """The whole fleet by plate, filtered in Python: the pilot has at most a thousand cars."""
    cars = await list_cars(db)
    return [car for car in cars if domain.matches_staff_filter(car, car_filter, rules)]


async def list_models(db: AsyncSession) -> list[StoredCarModel]:
    """Car models by brand and name; the pricing module lists a tariff for each."""
    return await _read(db, lambda: repository.list_models(db))


async def get_model(db: AsyncSession, model_id: UUID) -> StoredCarModel | None:
    return await _read(db, lambda: repository.get_model(db, model_id))


async def change_status(
    db: AsyncSession, car_id: UUID, transition: CarTransition, now: datetime
) -> CarStatus:
    """Moves the car along the status machine (fleet.domain) and holds its row until commit."""
    _require_transaction(db, "change_status")
    status = await repository.get_status_for_update(db, car_id)
    if status is None:
        raise CarNotFoundError
    new_status = apply_transition(status, transition)
    await repository.set_status(db, car_id, new_status, now)
    await repository.notify(db, encode_car_changes([car_id]))
    return new_status


async def reserve_car(
    db: AsyncSession, car_id: UUID, rules: AvailabilityRules, now: datetime
) -> Car:
    """Reserves the car for a rental if it may be offered to a client now; holds its row.

    The rule is checked under the row lock, so two reservations of one car never both pass.
    Raises CarNotFoundError for a car that is not rentable as well as for a missing one: the
    client map does not tell the reason either.
    """
    _require_transaction(db, "reserve_car")
    car = await repository.get_car(db, car_id, for_update=True)
    if car is None or not domain.is_rentable(car.status, car.state, rules, now):
        raise CarNotFoundError
    status = apply_transition(car.status, CarTransition.RESERVE)
    await repository.set_status(db, car.id, status, now)
    await repository.notify(db, encode_car_changes([car.id]))
    return replace(car, status=status)


async def apply_state_snapshot(
    db: AsyncSession, telematics_id: UUID, state: CarState, now: datetime, max_skew: timedelta
) -> SnapshotOutcome:
    """Stores a telematics snapshot unless a newer one is stored already.

    Raises SnapshotFromFutureError for a snapshot more than `max_skew` ahead of `now`.
    """
    _require_transaction(db, "apply_state_snapshot")
    outcome, car_id = await _apply_snapshot(db, telematics_id, state, now, max_skew)
    if car_id is not None:
        await repository.notify(db, encode_car_changes([car_id]))
    return outcome


async def _apply_snapshot(
    db: AsyncSession, telematics_id: UUID, state: CarState, now: datetime, max_skew: timedelta
) -> tuple[SnapshotOutcome, UUID | None]:
    check_snapshot_time(state.recorded_at, now, max_skew)
    return await repository.apply_state(db, telematics_id, state, now)


async def receive_telemetry(
    db: AsyncSession, snapshots: Sequence[TelemetrySnapshot], now: datetime, max_skew: timedelta
) -> TelemetryReport:
    """Applies a telemetry batch in one transaction of its own.

    A snapshot that cannot be applied is counted and skipped: one unknown car or one skewed clock
    must not hold back the state of the rest of the fleet.
    """
    counts = dict.fromkeys(SnapshotOutcome, 0)
    unknown: list[UUID] = []
    from_future = 0
    changed: list[UUID] = []
    async with db.begin():
        for snapshot in snapshots:
            try:
                outcome, car_id = await _apply_snapshot(
                    db, snapshot.telematics_id, snapshot.state, now, max_skew
                )
            except SnapshotFromFutureError:
                from_future += 1
                continue
            counts[outcome] += 1
            if car_id is not None:
                changed.append(car_id)
            if outcome is SnapshotOutcome.UNKNOWN_CAR:
                unknown.append(snapshot.telematics_id)
        # A few notices for the whole batch rather than one per car.
        await repository.notify(db, encode_car_changes(changed))
    report = TelemetryReport(
        applied=counts[SnapshotOutcome.APPLIED],
        stale=counts[SnapshotOutcome.STALE],
        unknown_car=counts[SnapshotOutcome.UNKNOWN_CAR],
        from_future=from_future,
    )
    if unknown or from_future:
        # Telematics ids only: the coordinates in the snapshots show where clients drive.
        logger.warning(
            "telemetry_partly_rejected",
            unknown_car=report.unknown_car,
            unknown_telematics_ids=[str(car_id) for car_id in unknown[:_LOGGED_UNKNOWN_CARS]],
            from_future=from_future,
        )
    return report


@dataclass(frozen=True)
class _Sent:
    command_id: UUID
    outcome: CommandOutcome
    state: CarState | None
    failure: AppError | None
    reason: str | None


async def _send(gateway: TelematicsGateway, car: Car, command: CarCommand) -> _Sent:
    command_id = uuid4()
    try:
        state = await gateway.send(car.telematics_id, command_id, command)
    except CarCommandRejectedError as error:
        return _Sent(command_id, CommandOutcome.REJECTED, None, error, error.reason)
    except CarCommandDuplicateError as error:
        return _Sent(command_id, CommandOutcome.DUPLICATE, None, error, None)
    except TelematicsUnavailableError as error:
        return _Sent(command_id, CommandOutcome.UNAVAILABLE, None, error, None)
    except TelematicsTimeoutError as error:
        return _Sent(command_id, CommandOutcome.TIMED_OUT, None, error, None)
    return _Sent(command_id, CommandOutcome.DONE, state, None, None)


async def _apply_command_state(
    db: AsyncSession, car: Car, state: CarState, now: datetime, max_skew: timedelta
) -> None:
    try:
        _, changed = await _apply_snapshot(db, car.telematics_id, state, now, max_skew)
    except SnapshotFromFutureError:
        # The command itself went through; the next telemetry brings the state.
        logger.warning("car_command_state_from_future", car_id=str(car.id))
        return
    if changed is not None:
        await repository.notify(db, encode_car_changes([changed]))


@dataclass(frozen=True)
class CarCommandRequest:
    car_id: UUID
    command: CarCommand
    # Why the staff member sends it: kept in the audit log, checked for personal data before.
    comment: str
    confirm_active_rental: bool


@dataclass(frozen=True)
class CarCommandResult:
    command_id: UUID
    car: Car


@dataclass(frozen=True)
class CarCommander:
    """Manual commands of one staff member (docs/02-architecture.md, the fleet section).

    Every command that went to telematics is audited and announced to staff, whatever its
    outcome. The car is not waited for inside a transaction: nothing holds a row or a connection
    while telematics thinks.
    """

    db: AsyncSession
    gateway: TelematicsGateway
    staff_id: UUID
    origin: RequestOrigin
    max_skew: timedelta

    async def send(self, request: CarCommandRequest, now: datetime) -> CarCommandResult:
        """Raises CarNotFoundError, CarInActiveRentalError without sending anything; after
        sending, the errors of TelematicsGateway.send, once the outcome is recorded.
        """
        car = await get_car(self.db, request.car_id)
        during_rental = domain.needs_rental_confirmation(car.status)
        if during_rental and not request.confirm_active_rental:
            raise CarInActiveRentalError
        sent = await _send(self.gateway, car, request.command)
        notice = CommandNotice(
            command_id=sent.command_id,
            car_id=car.id,
            command=request.command,
            outcome=sent.outcome,
            reason=sent.reason,
            staff_id=self.staff_id,
            at=now,
        )
        async with self.db.begin():
            if sent.state is not None:
                await _apply_command_state(self.db, car, sent.state, now, self.max_skew)
            await audit.record(self.db, self._entry(car, request, notice, during_rental))
            await repository.notify(self.db, [encode_command(notice)])
        logger.info(
            "car_command_sent",
            car_id=str(car.id),
            command=request.command,
            outcome=sent.outcome,
            reason=sent.reason,
        )
        if sent.failure is not None:
            raise sent.failure
        return CarCommandResult(command_id=sent.command_id, car=await get_car(self.db, car.id))

    def _entry(
        self, car: Car, request: CarCommandRequest, notice: CommandNotice, during_rental: bool
    ) -> AuditEntry:
        return AuditEntry(
            Actor(ActorType.STAFF, self.staff_id),
            AuditAction.CAR_COMMAND_SENT,
            self.origin,
            AuditTarget(AuditTargetType.CAR, car.id),
            details={
                "command": request.command.value,
                "outcome": notice.outcome.value,
                "reason": notice.reason,
                "command_id": notice.command_id,
                "car_status": car.status.value,
                "during_active_rental": during_rental,
                "comment": request.comment,
            },
        )


# What a client may ask of the car of their rental; the engine stays with staff.
CLIENT_COMMANDS = frozenset({CarCommand.OPEN, CarCommand.CLOSE})


@dataclass(frozen=True)
class RentalCommandRequest:
    car_id: UUID
    command: CarCommand
    client_id: UUID
    rental_id: UUID


@dataclass(frozen=True)
class RentalCarCommander:
    """Commands a client sends to the car of their active rental (docs/03-security.md).

    The rentals module checks that the client may send them; the fleet only sends, audits and
    applies the state the car reports. Nothing goes to the staff command stream: it lists manual
    commands, and the car's own change reaches staff as a car notice.
    """

    db: AsyncSession
    gateway: TelematicsGateway
    origin: RequestOrigin
    max_skew: timedelta

    async def send(self, request: RentalCommandRequest, now: datetime) -> Car:
        """Returns the car after the command. Raises CarNotFoundError without sending anything;
        after sending, the errors of TelematicsGateway.send, once the outcome is recorded.
        """
        command = request.command
        if command not in CLIENT_COMMANDS:
            raise ValueError(f"clients do not send {command}")
        car = await get_car(self.db, request.car_id)
        sent = await _send(self.gateway, car, command)
        async with self.db.begin():
            if sent.state is not None:
                await _apply_command_state(self.db, car, sent.state, now, self.max_skew)
            await audit.record(
                self.db,
                AuditEntry(
                    Actor(ActorType.CLIENT, request.client_id),
                    AuditAction.CAR_COMMAND_SENT,
                    self.origin,
                    AuditTarget(AuditTargetType.CAR, car.id),
                    details={
                        "command": command.value,
                        "outcome": sent.outcome.value,
                        "reason": sent.reason,
                        "command_id": sent.command_id,
                        "car_status": car.status.value,
                        "rental_id": request.rental_id,
                    },
                ),
            )
        logger.info(
            "car_command_sent",
            car_id=str(car.id),
            command=command,
            outcome=sent.outcome,
            reason=sent.reason,
            by="client",
        )
        if sent.failure is not None:
            raise sent.failure
        return await get_car(self.db, car.id)


@dataclass(frozen=True)
class SeedResult:
    models_created: int
    cars_created: int
    cars_existing: int


async def seed_demo_fleet(
    db: AsyncSession, emulator_seed: int, car_count: int, now: datetime
) -> SeedResult:
    """Adds the demo models and cars; safe to run again, existing rows stay as they are.

    Cars start `available` without state: they become rentable once the emulator's telemetry
    arrives for them. Runs in a transaction of its own, so nothing is added on a failure.
    Raises CarIdentityTakenError if the demo cars exist under other telematics ids, i.e. they were
    seeded for another EMULATOR_SEED.
    """
    if not 0 < car_count <= MAX_DEMO_CARS:
        raise ValueError(f"car_count must be within 1..{MAX_DEMO_CARS}")
    async with db.begin():
        model_ids: dict[int, UUID] = {}
        models_created = 0
        cars_created = 0
        for index in range(car_count):
            key = index % len(DEMO_MODELS)
            if key not in model_ids:
                spec = DEMO_MODELS[key].spec
                model_ids[key], created = await repository.ensure_model(db, spec, now)
                models_created += created
            car = repository.NewCar(
                model_id=model_ids[key],
                plate=normalize_plate(demo_plate(index)),
                vin=normalize_vin(demo_vin(index)),
                telematics_id=emulator_car_id(emulator_seed, index),
                status=CarStatus.AVAILABLE,
            )
            cars_created += await repository.add_car_if_absent(db, car, now)
    return SeedResult(models_created, cars_created, car_count - cars_created)


def _require_transaction(db: AsyncSession, name: str) -> None:
    if not db.in_transaction():
        raise RuntimeError(f"fleet.service.{name} must run inside the caller's transaction")
