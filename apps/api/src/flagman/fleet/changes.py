"""Notices about fleet changes that every API process passes on to staff (docs/adr/0017).

A car notice says only which cars changed: each process reads their state itself, after the
commit, so it never sends a state older than the database's. A command notice is whole: there is
nothing to read back. No I/O here.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from flagman.fleet.domain import CarCommand, CommandOutcome

# Postgres notifications are sent on commit only and to every listening process.
CHANNEL = "fleet_changes"
# Postgres caps a notification at 8000 bytes; this many ids of 36 characters stay well under it.
CARS_PER_NOTICE = 150


@dataclass(frozen=True)
class CommandNotice:
    """A manual command and how it ended, for every staff member watching the fleet."""

    command_id: UUID
    car_id: UUID
    command: CarCommand
    outcome: CommandOutcome
    reason: str | None
    staff_id: UUID
    at: datetime


@dataclass(frozen=True)
class FleetNotice:
    car_ids: frozenset[UUID] = field(default_factory=frozenset)
    # In commit order.
    commands: tuple[CommandNotice, ...] = ()


def encode_car_changes(car_ids: Iterable[UUID]) -> list[str]:
    ids = sorted({str(car_id) for car_id in car_ids})
    return [
        json.dumps({"cars": ids[start : start + CARS_PER_NOTICE]})
        for start in range(0, len(ids), CARS_PER_NOTICE)
    ]


def encode_command(notice: CommandNotice) -> str:
    return json.dumps(
        {
            "command": {
                "command_id": str(notice.command_id),
                "car_id": str(notice.car_id),
                "command": notice.command.value,
                "outcome": notice.outcome.value,
                "reason": notice.reason,
                "staff_id": str(notice.staff_id),
                "at": notice.at.isoformat(),
            }
        }
    )


def _command(data: dict[str, Any]) -> CommandNotice:
    reason = data["reason"]
    at = datetime.fromisoformat(data["at"])
    if (reason is not None and not isinstance(reason, str)) or at.tzinfo is None:
        raise ValueError("not a command notice")
    return CommandNotice(
        command_id=UUID(data["command_id"]),
        car_id=UUID(data["car_id"]),
        command=CarCommand(data["command"]),
        outcome=CommandOutcome(data["outcome"]),
        reason=reason,
        staff_id=UUID(data["staff_id"]),
        at=at,
    )


def decode_notices(payloads: Sequence[str]) -> FleetNotice:
    """Merges the notices that came while the previous ones were handled.

    Raises ValueError for a payload that is not a notice: only the API writes to the channel,
    so that is a bug, not input to skip.
    """
    car_ids: set[UUID] = set()
    commands: list[CommandNotice] = []
    for payload in payloads:
        data = json.loads(payload)
        if isinstance(data, dict) and isinstance(data.get("cars"), list):
            car_ids.update(UUID(car_id) for car_id in data["cars"])
        elif isinstance(data, dict) and isinstance(data.get("command"), dict):
            try:
                commands.append(_command(data["command"]))
            except (KeyError, TypeError) as error:
                raise ValueError("not a command notice") from error
        else:
            raise ValueError("not a fleet notice")
    return FleetNotice(car_ids=frozenset(car_ids), commands=tuple(commands))
