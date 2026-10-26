"""`flagman-worker`: periodic jobs that run next to the API, see docs/adr/0013 and docs/adr/0018.

    flagman-worker

Runs until stopped (SIGTERM, Ctrl+C). Several copies may run at once: a Postgres advisory lock
lets only one of them do a job at a time.
"""

import asyncio
import signal
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flagman.core.config import WorkerSettings, load_worker_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.logging import configure_logging
from flagman.identity import service as identity
from flagman.rentals import service as rentals

logger = structlog.get_logger(__name__)

AUTH_CLEANUP = "auth_cleanup"
RESERVATION_EXPIRY = "reservation_expiry"
INTERVALS: Mapping[str, timedelta] = {
    AUTH_CLEANUP: timedelta(hours=1),
    # A reservation is cancelled within a minute of its end even if nobody asks for it, so the
    # car is back on the map (docs/adr/0018).
    RESERVATION_EXPIRY: timedelta(minutes=1),
}
# Sooner after a failure: on a fresh database the first run comes before the migrations, and
# waiting a whole hour after them would look like the worker does nothing.
RETRY_AFTER = timedelta(minutes=1)
# Any fixed numbers unique within the database; the next job takes the next one.
AUTH_CLEANUP_LOCK = 4_600_001
RESERVATION_EXPIRY_LOCK = 4_600_002


async def _with_lock[T](
    engine: AsyncEngine, key: int, job: str, work: Callable[[], Awaitable[T]]
) -> T | None:
    """Runs `work` unless another copy of the worker holds the job; then returns None."""
    # A session-level lock on a connection of its own: the jobs commit batch by batch, which
    # would release a transaction-level lock after the first one. If the worker dies, Postgres
    # drops the connection and the lock with it.
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        )
        await lock_connection.commit()
        if not locked:
            logger.info(f"{job}_skipped", reason="another worker holds the job")
            return None
        try:
            return await work()
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await lock_connection.commit()


async def run_auth_cleanup(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
    now: datetime,
) -> identity.AuthCleanupResult | None:
    """Returns None when another copy of the worker holds the job."""

    async def clean_up() -> identity.AuthCleanupResult:
        async with session_factory() as db:
            return await identity.clean_up_auth_data(
                db, now, timedelta(days=settings.auth_cleanup_retention_days)
            )

    result = await _with_lock(engine, AUTH_CLEANUP_LOCK, AUTH_CLEANUP, clean_up)
    if result is not None:
        # Counts only: the rows hold phones and session ids.
        logger.info(
            "auth_cleanup_done",
            pending_otps=result.pending_otps,
            client_sessions=result.client_sessions,
            staff_sessions=result.staff_sessions,
        )
    return result


async def run_reservation_expiry(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
    now: datetime,
) -> int | None:
    """Returns how many reservations were cancelled, None when another copy holds the job."""

    async def expire() -> int:
        async with session_factory() as db:
            return await rentals.expire_reservations(db, now)

    expired = await _with_lock(engine, RESERVATION_EXPIRY_LOCK, RESERVATION_EXPIRY, expire)
    # Every minute: only the runs that did something are worth a line in the log.
    if expired:
        logger.info("reservation_expiry_done", expired=expired)
    return expired


Job = Callable[
    [AsyncEngine, async_sessionmaker[AsyncSession], WorkerSettings, datetime], Awaitable[object]
]


@dataclass(frozen=True)
class _Scheduled:
    name: str
    interval: timedelta
    job: Job


def _schedule(intervals: Mapping[str, timedelta]) -> list[_Scheduled]:
    # The jobs are looked up when the worker starts, so tests can replace them.
    jobs: dict[str, Job] = {
        AUTH_CLEANUP: run_auth_cleanup,
        RESERVATION_EXPIRY: run_reservation_expiry,
    }
    return [_Scheduled(name, intervals[name], job) for name, job in jobs.items()]


async def run(
    settings: WorkerSettings,
    stop: asyncio.Event,
    intervals: Mapping[str, timedelta] = INTERVALS,
    retry_after: timedelta = RETRY_AFTER,
) -> None:
    """Runs every job at once and then every its interval (`retry_after` after a failure),
    until `stop` is set."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    schedule = _schedule(intervals)
    due = {scheduled.name: time.monotonic() for scheduled in schedule}
    try:
        while not stop.is_set():
            for scheduled in schedule:
                if time.monotonic() < due[scheduled.name] or stop.is_set():
                    continue
                wait = scheduled.interval
                try:
                    await scheduled.job(engine, session_factory, settings, datetime.now(UTC))
                # Postgres restarting or tables not migrated yet must not stop the worker.
                except Exception:
                    logger.exception(
                        f"{scheduled.name}_failed",
                        retry_in_seconds=int(retry_after.total_seconds()),
                    )
                    wait = retry_after
                due[scheduled.name] = time.monotonic() + wait.total_seconds()
            sleep = max(0.0, min(due.values()) - time.monotonic())
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), sleep)
    finally:
        await engine.dispose()


async def _main() -> None:
    settings = load_worker_settings()
    configure_logging(settings.api_log_level)
    stop = asyncio.Event()
    # Docker stops containers with SIGTERM. Windows has no signal handlers in asyncio, but Ctrl+C
    # still ends asyncio.run there.
    with suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    logger.info(
        "worker_started",
        intervals_seconds={
            name: int(interval.total_seconds()) for name, interval in INTERVALS.items()
        },
    )
    await run(settings, stop)
    logger.info("worker_stopped")


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_main())
