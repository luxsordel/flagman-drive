"""`flagman-worker`: periodic jobs that run next to the API, see docs/adr/0013.

    flagman-worker

Runs until stopped (SIGTERM, Ctrl+C). Several copies may run at once: a Postgres advisory lock
lets only one of them do a job at a time.
"""

import asyncio
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flagman.core.config import WorkerSettings, load_worker_settings
from flagman.core.db import create_engine, create_session_factory
from flagman.core.logging import configure_logging
from flagman.identity import service as identity

logger = structlog.get_logger(__name__)

JOB_INTERVAL = timedelta(hours=1)
# Sooner after a failure: on a fresh database the first run comes before the migrations, and
# waiting a whole hour after them would look like the worker does nothing.
RETRY_AFTER = timedelta(minutes=1)
# Any fixed number unique within the database; the next job takes the next one.
AUTH_CLEANUP_LOCK = 4_600_001


async def run_auth_cleanup(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    settings: WorkerSettings,
    now: datetime,
) -> identity.AuthCleanupResult | None:
    """Returns None when another copy of the worker holds the job."""
    # A session-level lock on a connection of its own: the cleanup commits batch by batch, which
    # would release a transaction-level lock after the first one. If the worker dies, Postgres
    # drops the connection and the lock with it.
    async with engine.connect() as lock_connection:
        locked = await lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": AUTH_CLEANUP_LOCK}
        )
        await lock_connection.commit()
        if not locked:
            logger.info("auth_cleanup_skipped", reason="another worker holds the job")
            return None
        try:
            async with session_factory() as db:
                result = await identity.clean_up_auth_data(
                    db, now, timedelta(days=settings.auth_cleanup_retention_days)
                )
        finally:
            await lock_connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": AUTH_CLEANUP_LOCK}
            )
            await lock_connection.commit()
    # Counts only: the rows hold phones and session ids.
    logger.info(
        "auth_cleanup_done",
        pending_otps=result.pending_otps,
        client_sessions=result.client_sessions,
        staff_sessions=result.staff_sessions,
    )
    return result


async def run(
    settings: WorkerSettings,
    stop: asyncio.Event,
    interval: timedelta = JOB_INTERVAL,
    retry_after: timedelta = RETRY_AFTER,
) -> None:
    """Runs the jobs at once and then every `interval` (`retry_after` after a failure), until
    `stop` is set."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        while not stop.is_set():
            wait = interval
            try:
                await run_auth_cleanup(engine, session_factory, settings, datetime.now(UTC))
            # Postgres restarting or tables not migrated yet must not stop the worker.
            except Exception:
                logger.exception(
                    "auth_cleanup_failed", retry_in_seconds=int(retry_after.total_seconds())
                )
                wait = retry_after
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), wait.total_seconds())
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
    logger.info("worker_started", interval_seconds=int(JOB_INTERVAL.total_seconds()))
    await run(settings, stop)
    logger.info("worker_stopped")


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(_main())
