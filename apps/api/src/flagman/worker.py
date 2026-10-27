"""`flagman-worker`: periodic jobs that run next to the API, see docs/adr/0013 and 0020.

    flagman-worker

Runs until stopped (SIGTERM, Ctrl+C). Several copies may run at once: a Postgres advisory lock
lets only one of them do a job at a time.
"""

import asyncio
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flagman.core.config import WorkerSettings, load_worker_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.logging import configure_logging
from flagman.identity import service as identity
from flagman.notifications import service as notifications
from flagman.notifications.expo import ExpoPushProvider
from flagman.notifications.fake_provider import FakePushProvider
from flagman.notifications.ports import PushProvider

logger = structlog.get_logger(__name__)

JOB_INTERVAL = timedelta(hours=1)
# Sooner after a failure: on a fresh database the first run comes before the migrations, and
# waiting a whole hour after them would look like the worker does nothing.
RETRY_AFTER = timedelta(minutes=1)
# Any fixed numbers unique within the database; the next job takes the next one.
AUTH_CLEANUP_LOCK = 4_600_001
NOTIFICATIONS_DISPATCH_LOCK = 4_600_002
OUTBOX_CLEANUP_LOCK = 4_600_003


@asynccontextmanager
async def _job_lock(engine: AsyncEngine, key: int, job: str) -> AsyncIterator[bool]:
    """Yields False when another copy of the worker holds the job."""
    # A session-level lock on a connection of its own: a job may commit several times, which
    # would release a transaction-level lock after the first. If the worker dies, Postgres drops
    # the connection and the lock with it.
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        )
        await lock_connection.commit()
        if not locked:
            logger.info(f"{job}_skipped", reason="another worker holds the job")
            yield False
            return
        try:
            yield True
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
    async with _job_lock(engine, AUTH_CLEANUP_LOCK, "auth_cleanup") as locked:
        if not locked:
            return None
        async with session_factory() as db:
            result = await identity.clean_up_auth_data(
                db, now, timedelta(days=settings.auth_cleanup_retention_days)
            )
    # Counts only: the rows hold phones and session ids.
    logger.info(
        "auth_cleanup_done",
        pending_otps=result.pending_otps,
        client_sessions=result.client_sessions,
        staff_sessions=result.staff_sessions,
    )
    return result


async def run_outbox_cleanup(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
    now: datetime,
) -> int | None:
    """Returns None when another copy of the worker holds the job."""
    async with _job_lock(engine, OUTBOX_CLEANUP_LOCK, "outbox_cleanup") as locked:
        if not locked:
            return None
        async with session_factory() as db:
            deleted = await notifications.clean_up_outbox(
                db, now, timedelta(days=settings.notifications_retention_days)
            )
    logger.info("outbox_cleanup_done", notifications=deleted)
    return deleted


async def run_notifications_dispatch(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    provider: PushProvider,
    now: datetime,
) -> notifications.DispatchResult | None:
    """Returns None when another copy of the worker holds the job."""
    async with _job_lock(engine, NOTIFICATIONS_DISPATCH_LOCK, "notifications_dispatch") as locked:
        if not locked:
            return None
        return await notifications.dispatch_due(session_factory, provider, now)


def push_provider(settings: WorkerSettings, http: httpx2.AsyncClient) -> PushProvider | None:
    match settings.notifications_provider:
        case "expo":
            token = settings.expo_access_token
            return ExpoPushProvider(
                client=http,
                access_token=token.get_secret_value() if token else None,
                timeout_s=settings.expo_push_timeout_seconds,
            )
        case "fake":
            return FakePushProvider(record=False)
        case None:
            return None


@dataclass
class _Job:
    name: str
    interval: timedelta
    retry_after: timedelta
    run: Callable[[datetime], Awaitable[object]]
    due: datetime


async def run(
    settings: WorkerSettings,
    stop: asyncio.Event,
    interval: timedelta = JOB_INTERVAL,
    retry_after: timedelta = RETRY_AFTER,
) -> None:
    """Runs the jobs at once and then each on its own interval (`interval` for the cleanups,
    `retry_after` after a failure), until `stop` is set."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with httpx2.AsyncClient() as http:
            provider = push_provider(settings, http)
            start = datetime.now(UTC)
            jobs = [
                _Job(
                    "auth_cleanup",
                    interval,
                    retry_after,
                    lambda now: run_auth_cleanup(engine, session_factory, settings, now),
                    start,
                ),
                _Job(
                    "outbox_cleanup",
                    interval,
                    retry_after,
                    lambda now: run_outbox_cleanup(engine, session_factory, settings, now),
                    start,
                ),
            ]
            if provider is None:
                logger.warning("notifications_not_sent", reason="NOTIFICATIONS_PROVIDER is not set")
            else:
                dispatch_interval = timedelta(
                    seconds=settings.notifications_dispatch_interval_seconds
                )
                jobs.append(
                    _Job(
                        "notifications_dispatch",
                        dispatch_interval,
                        min(retry_after, dispatch_interval),
                        lambda now: run_notifications_dispatch(
                            engine, session_factory, provider, now
                        ),
                        start,
                    )
                )
            await _loop(jobs, stop)
    finally:
        await engine.dispose()


async def _loop(jobs: list[_Job], stop: asyncio.Event) -> None:
    while not stop.is_set():
        for job in jobs:
            now = datetime.now(UTC)
            if job.due > now or stop.is_set():
                continue
            try:
                await job.run(now)
                job.due = now + job.interval
            # Postgres restarting or tables not migrated yet must not stop the worker.
            except Exception:
                logger.exception(
                    f"{job.name}_failed", retry_in_seconds=int(job.retry_after.total_seconds())
                )
                job.due = now + job.retry_after
        wait = min(job.due for job in jobs) - datetime.now(UTC)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), max(wait.total_seconds(), 0))


async def _main() -> None:
    settings = load_worker_settings()
    configure_logging(settings.api_log_level)
    stop = asyncio.Event()
    # Docker stops containers with SIGTERM. Windows has no signal handlers in asyncio, but Ctrl+C
    # still ends asyncio.run there.
    with suppress(NotImplementedError):
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    logger.info("worker_started", interval_seconds=int(JOB_INTERVAL.total_seconds()))
    await run(settings, stop)
    logger.info("worker_stopped")


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_main())
