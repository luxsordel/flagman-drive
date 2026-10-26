import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.geo import GeoPoint
from flagman.fleet import repository
from flagman.fleet.domain import CarCommand, CarModelSpec, CarState, CarStatus
from flagman.fleet.models import Car as CarRow
from flagman.fleet.seed import DEMO_MODELS
from flagman.identity import service as identity
from flagman.identity.domain import (
    AccessClaims,
    StaffAccessClaims,
    StaffRole,
    issue_access_token,
    issue_staff_access_token,
)

_PLATE_LETTERS = "ABEIKMHOPCTX"
_VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
STAFF_PASSWORD = "correct horse battery staple"


def add_car(  # noqa: PLR0913
    settings: Settings,
    point: GeoPoint | None,
    *,
    status: CarStatus = CarStatus.AVAILABLE,
    battery_pct: int = 80,
    age: timedelta = timedelta(seconds=5),
    model_index: int = 0,
    engine_locked: bool = False,
    model: CarModelSpec | None = None,
) -> str:
    """Adds a car with a random plate and VIN, with a snapshot at `point` unless it is None."""

    async def add() -> uuid.UUID:
        rng = random.SystemRandom()
        engine = create_engine(settings)
        now = datetime.now(UTC)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                spec = model or DEMO_MODELS[model_index].spec
                model_id, _ = await repository.ensure_model(db, spec, now)
                telematics_id = uuid.uuid4()
                car = repository.NewCar(
                    model_id=model_id,
                    plate=f"{rng.randrange(10_000):04d} {''.join(rng.choices(_PLATE_LETTERS, k=2))}"
                    f"-{rng.randint(1, 6)}",
                    vin="".join(rng.choices(_VIN_CHARS, k=17)),
                    telematics_id=telematics_id,
                    status=status,
                )
                assert await repository.add_car_if_absent(db, car, now)
                if point is not None:
                    state = CarState(point, battery_pct, True, False, engine_locked, now - age)
                    await repository.apply_state(db, telematics_id, state, now)
                car_id = await db.scalar(
                    select(CarRow.id).where(CarRow.telematics_id == telematics_id)
                )
                assert car_id is not None
                return car_id
        finally:
            await engine.dispose()

    return str(asyncio.run(add()))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def client_token(settings: Settings) -> str:
    secret = settings.api_jwt_secret.get_secret_value()
    return issue_access_token(AccessClaims(uuid.uuid4(), uuid.uuid4()), secret, datetime.now(UTC))


def staff_token(
    settings: Settings,
    role: StaffRole,
    *,
    staff_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> str:
    """A token of a staff member who may not exist: only endpoints that read the database care."""
    claims = StaffAccessClaims(staff_id or uuid.uuid4(), session_id or uuid.uuid4(), role)
    secret = settings.api_jwt_secret.get_secret_value()
    return issue_staff_access_token(claims, secret, now or datetime.now(UTC))


def run_in_db[T](settings: Settings, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def run() -> T:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await work(db)
        finally:
            await engine.dispose()

    return asyncio.run(run())


class Staff:
    """A staff member with a real session: the stream and car commands check it in the database."""

    def __init__(self, settings: Settings, role: StaffRole = StaffRole.OPERATOR) -> None:
        self.settings = settings
        self.email = f"fleet-{uuid.uuid4().hex[:12]}@example.com"
        now = datetime.now(UTC)
        self.id = run_in_db(
            settings, lambda db: identity.create_staff(db, self.email, STAFF_PASSWORD, role, now)
        )
        self.session_id = uuid.uuid4()
        self.role = role
        run_in_db(settings, self._add_session)

    async def _add_session(self, db: AsyncSession) -> None:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO staff_sessions (id, staff_id, created_at, expires_at) "
                    "VALUES (:id, :staff_id, now(), now() + interval '12 hours')"
                ),
                {"id": self.session_id, "staff_id": self.id},
            )

    def token(self, now: datetime | None = None) -> str:
        return staff_token(
            self.settings, self.role, staff_id=self.id, session_id=self.session_id, now=now
        )

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.token())

    def revoke_session(self) -> None:
        async def revoke(db: AsyncSession) -> None:
            async with db.begin():
                await db.execute(
                    text("UPDATE staff_sessions SET revoked_at = now() WHERE id = :id"),
                    {"id": self.session_id},
                )

        run_in_db(self.settings, revoke)

    def disable(self) -> None:
        run_in_db(
            self.settings, lambda db: identity.disable_staff(db, self.email, datetime.now(UTC))
        )


@dataclass
class FakeGateway:
    """Answers like telematics would; `outcome` is the state after or the error to raise."""

    outcome: CarState | Exception | None = None
    calls: list[tuple[uuid.UUID, uuid.UUID, CarCommand]] = field(default_factory=list)

    async def send(
        self, telematics_id: uuid.UUID, command_id: uuid.UUID, command: CarCommand
    ) -> CarState | None:
        self.calls.append((telematics_id, command_id, command))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome
