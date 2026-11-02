"""The staff name clients see in the support chat (#120): `flagman-staff set-name`."""

import asyncio
import io
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from flagman.core.config import Settings
from flagman.core.db import create_engine, create_session_factory
from flagman.identity import cli, service
from flagman.identity.domain import StaffRole

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


def _create_staff(settings: Settings, email: str) -> uuid.UUID:
    async def run() -> uuid.UUID:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db:
                return await service.create_staff(
                    db, email, PASSWORD, StaffRole.OPERATOR, datetime.now(UTC)
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _names_and_audit(
    settings: Settings, staff_id: uuid.UUID
) -> tuple[dict[uuid.UUID, str], list[str]]:
    async def run() -> tuple[dict[uuid.UUID, str], list[str]]:
        engine = create_engine(settings)
        try:
            async with create_session_factory(engine)() as db, db.begin():
                names = await service.get_staff_display_names(db, {staff_id, uuid.uuid4()})
                actions = await db.scalars(
                    text(
                        "SELECT action FROM audit_events WHERE target_id = :id "
                        "ORDER BY occurred_at, id"
                    ),
                    {"id": staff_id},
                )
                return names, [str(action) for action in actions]
        finally:
            await engine.dispose()

    return asyncio.run(run())


@pytest.fixture
def staff_cli(integration_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda: integration_settings)


def test_a_name_is_set_and_shown_only_for_named_staff(
    integration_settings: Settings, staff_cli: None
) -> None:
    email = f"name-{uuid.uuid4().hex[:8]}@example.com"
    staff_id = _create_staff(integration_settings, email)
    assert _names_and_audit(integration_settings, staff_id)[0] == {}
    stdout = io.StringIO()

    code = cli.main(["set-name", "--email", email, "--name", "  Anna  "], stdout=stdout)

    assert code == cli.EXIT_OK
    assert str(staff_id) in stdout.getvalue()
    names, actions = _names_and_audit(integration_settings, staff_id)
    assert names == {staff_id: "Anna"}
    assert actions == ["staff.created", "staff.name_changed"]


def test_an_unknown_email_gets_no_name(staff_cli: None, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["set-name", "--email", "nobody@example.com", "--name", "Anna"])

    assert code == cli.EXIT_FAILED
    assert "no staff member" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["   ", "An\u200bna", "x" * 101])
def test_a_broken_name_is_refused_before_the_database(name: str) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["set-name", "--email", "operator@example.com", "--name", name])

    assert exited.value.code == 2
