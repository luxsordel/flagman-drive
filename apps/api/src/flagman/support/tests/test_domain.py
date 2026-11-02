import base64
import uuid
from datetime import UTC, datetime

import pytest

from flagman.support.domain import (
    ConversationTakenError,
    IncidentCommentRequiredError,
    IncidentCursor,
    IncidentNotYoursError,
    IncidentStatus,
    IncidentTransition,
    IncidentTransitionNotAllowedError,
    apply_incident_transition,
    ensure_conversation_owner,
    ensure_incident_owner,
    is_queue,
)

_OWNER, _OTHER = uuid.uuid4(), uuid.uuid4()


@pytest.mark.parametrize(
    ("current", "transition", "expected"),
    [
        (IncidentStatus.NEW, IncidentTransition.TAKE, IncidentStatus.IN_PROGRESS),
        (IncidentStatus.IN_PROGRESS, IncidentTransition.RESOLVE, IncidentStatus.RESOLVED),
        (IncidentStatus.IN_PROGRESS, IncidentTransition.REJECT, IncidentStatus.REJECTED),
    ],
)
def test_allowed_transitions(
    current: IncidentStatus, transition: IncidentTransition, expected: IncidentStatus
) -> None:
    assert apply_incident_transition(current, transition, "checked the photos") is expected


@pytest.mark.parametrize(
    ("current", "transition"),
    [
        # Nobody owns a new incident yet: it is taken before it is decided.
        (IncidentStatus.NEW, IncidentTransition.RESOLVE),
        (IncidentStatus.NEW, IncidentTransition.REJECT),
        (IncidentStatus.IN_PROGRESS, IncidentTransition.TAKE),
        (IncidentStatus.RESOLVED, IncidentTransition.TAKE),
        (IncidentStatus.RESOLVED, IncidentTransition.REJECT),
        (IncidentStatus.REJECTED, IncidentTransition.RESOLVE),
    ],
)
def test_refused_transitions(current: IncidentStatus, transition: IncidentTransition) -> None:
    with pytest.raises(IncidentTransitionNotAllowedError):
        apply_incident_transition(current, transition, "comment")


@pytest.mark.parametrize("transition", [IncidentTransition.RESOLVE, IncidentTransition.REJECT])
@pytest.mark.parametrize("comment", [None, "", "   "])
def test_closing_an_incident_needs_a_comment(
    transition: IncidentTransition, comment: str | None
) -> None:
    with pytest.raises(IncidentCommentRequiredError):
        apply_incident_transition(IncidentStatus.IN_PROGRESS, transition, comment)


def test_taking_needs_no_comment() -> None:
    assert (
        apply_incident_transition(IncidentStatus.NEW, IncidentTransition.TAKE, None)
        is IncidentStatus.IN_PROGRESS
    )


def test_only_the_owner_or_an_admin_acts_on_an_incident_in_work() -> None:
    owner, other = uuid.uuid4(), uuid.uuid4()

    ensure_incident_owner(owner, owner, admin=False)
    ensure_incident_owner(None, other, admin=False)
    ensure_incident_owner(owner, other, admin=True)
    with pytest.raises(IncidentNotYoursError):
        ensure_incident_owner(owner, other, admin=False)


@pytest.mark.parametrize(
    ("assignee", "staff", "admin"),
    [(_OWNER, _OWNER, False), (None, _OTHER, False), (_OWNER, _OTHER, True)],
)
def test_a_conversation_is_answered_by_its_operator_or_an_admin(
    assignee: uuid.UUID | None, staff: uuid.UUID, admin: bool
) -> None:
    ensure_conversation_owner(assignee, staff, admin=admin)


def test_another_operator_does_not_answer_a_taken_conversation() -> None:
    with pytest.raises(ConversationTakenError):
        ensure_conversation_owner(_OWNER, _OTHER, admin=False)


def test_a_cursor_comes_back_as_it_was_encoded() -> None:
    cursor = IncidentCursor(0, datetime(2026, 10, 29, 12, 30, tzinfo=UTC), uuid.uuid4())

    assert IncidentCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not base64 at all!",
        base64.urlsafe_b64encode(b"x|2026-10-29T12:30:00+00:00|" + str(_OWNER).encode()).decode(),
        # A time without an offset would mean different moments to different readers.
        base64.urlsafe_b64encode(b"1|2026-10-29T12:30:00|" + str(_OWNER).encode()).decode(),
        base64.urlsafe_b64encode(b"1|2026-10-29T12:30:00+00:00").decode(),
    ],
)
def test_a_broken_cursor_is_refused(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid cursor"):
        IncidentCursor.decode(raw)


@pytest.mark.parametrize(
    ("statuses", "queue"),
    [
        (None, False),
        (frozenset(), False),
        (frozenset({IncidentStatus.NEW}), True),
        (frozenset({IncidentStatus.NEW, IncidentStatus.IN_PROGRESS}), True),
        (frozenset({IncidentStatus.NEW, IncidentStatus.RESOLVED}), False),
        (frozenset({IncidentStatus.REJECTED}), False),
    ],
)
def test_only_open_incidents_make_a_queue(
    statuses: frozenset[IncidentStatus] | None, queue: bool
) -> None:
    assert is_queue(statuses) is queue
