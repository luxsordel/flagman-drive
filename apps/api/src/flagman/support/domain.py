"""Support rules: conversations, incidents and fines, their statuses and who may do what.

See docs/adr/0023 and docs/adr/0027.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from uuid import UUID

from flagman.core.errors import AppError

MESSAGE_TEXT_MAX_LENGTH = 2000
INCIDENT_DESCRIPTION_MAX_LENGTH = 2000
INCIDENT_COMMENT_MAX_LENGTH = 1000
MAX_INCIDENT_PHOTOS = 8
# How many open incidents one client can have at once: a flood of them would bury the queue.
MAX_OPEN_INCIDENTS_PER_CLIENT = 5
SIGNED_URL_TTL = timedelta(minutes=5)
POLL_PAGE_SIZE_MAX = 100
# The column is a BIGINT: a larger number would fail in the database instead of in validation.
SEQ_MAX = 2**63 - 1
QUEUE_SIZE_MAX = 100


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class MessageAuthor(StrEnum):
    CLIENT = "client"
    STAFF = "staff"


class MessageKind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"


class IncidentType(StrEnum):
    ACCIDENT = "accident"
    DAMAGE = "damage"
    MALFUNCTION = "malfunction"
    OTHER = "other"


class IncidentStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class IncidentTransition(StrEnum):
    TAKE = "take"
    RESOLVE = "resolve"
    REJECT = "reject"


class IncidentEventAction(StrEnum):
    """A line of the incident's chronology in the admin panel."""

    TAKE = "take"
    RESOLVE = "resolve"
    REJECT = "reject"
    SENT_TO_SERVICE = "sent_to_service"


@dataclass(frozen=True)
class _Rule:
    sources: frozenset[IncidentStatus]
    target: IncidentStatus


# Resolving or rejecting an incident nobody has taken is not allowed: a decision needs an owner.
_RULES: dict[IncidentTransition, _Rule] = {
    IncidentTransition.TAKE: _Rule(frozenset({IncidentStatus.NEW}), IncidentStatus.IN_PROGRESS),
    IncidentTransition.RESOLVE: _Rule(
        frozenset({IncidentStatus.IN_PROGRESS}), IncidentStatus.RESOLVED
    ),
    IncidentTransition.REJECT: _Rule(
        frozenset({IncidentStatus.IN_PROGRESS}), IncidentStatus.REJECTED
    ),
}

OPEN_INCIDENT_STATUSES = frozenset({IncidentStatus.NEW, IncidentStatus.IN_PROGRESS})
# Transitions that end the incident and therefore need the staff member's comment.
_NEEDS_COMMENT = frozenset({IncidentTransition.RESOLVE, IncidentTransition.REJECT})


class ConversationNotFoundError(AppError):
    """Also for another client's conversation: its existence is not told."""

    status = HTTPStatus.NOT_FOUND
    slug = "conversation-not-found"
    title = "Conversation not found"


class ConversationClosedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "conversation-closed"
    title = "The conversation is closed"


class ConversationTakenError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "conversation-taken"
    title = "Another staff member has taken this conversation"


class MessageNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "message-not-found"
    title = "Message not found"


class SupportRentalNotFoundError(AppError):
    """The rental the client attaches is not theirs or does not exist."""

    status = HTTPStatus.NOT_FOUND
    slug = "rental-not-found"
    title = "Rental not found"


class IncidentNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "incident-not-found"
    title = "Incident not found"


class IncidentTransitionNotAllowedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "incident-transition-not-allowed"
    title = "The incident cannot do this in its current status"


class IncidentCommentRequiredError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "incident-comment-required"
    title = "A comment is required to resolve or reject an incident"


class IncidentNotYoursError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "incident-not-yours"
    title = "Another staff member is working on this incident"


class IncidentPhotoLimitError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "incident-photo-limit"
    title = "The incident already has the largest number of photos"


class IncidentPhotoNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "incident-photo-not-found"
    title = "Photo not found"


class TooManyOpenIncidentsError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "too-many-open-incidents"
    title = "Too many incidents are open"


class IncidentClosedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "incident-closed"
    title = "The incident is closed"


class SupportPhotoTooLargeError(AppError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    slug = "support-photo-too-large"
    title = "The photo is too large"


class SupportPhotoTypeUnsupportedError(AppError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    slug = "support-photo-type-unsupported"
    title = "The photo must be a JPEG, PNG or WebP image"


class SupportPhotoStorageUnavailableError(AppError):
    status = HTTPStatus.SERVICE_UNAVAILABLE
    slug = "photo-storage-unavailable"
    title = "Photo storage is unavailable"


def apply_incident_transition(
    current: IncidentStatus,
    transition: IncidentTransition,
    comment: str | None,
) -> IncidentStatus:
    """The only way an incident changes its status."""
    rule = _RULES[transition]
    if current not in rule.sources:
        raise IncidentTransitionNotAllowedError
    if transition in _NEEDS_COMMENT and not (comment and comment.strip()):
        raise IncidentCommentRequiredError
    return rule.target


def ensure_incident_owner(assignee_id: UUID | None, staff_id: UUID, *, admin: bool) -> None:
    """An incident in work belongs to one staff member; an admin may take over any."""
    if assignee_id is not None and assignee_id != staff_id and not admin:
        raise IncidentNotYoursError


def ensure_conversation_owner(assignee_id: UUID | None, staff_id: UUID, *, admin: bool) -> None:
    """Two operators do not answer one client: a taken conversation is its operator's to answer
    and close; an admin may step in."""
    if assignee_id is not None and assignee_id != staff_id and not admin:
        raise ConversationTakenError


@dataclass(frozen=True)
class IncidentCursor:
    """Position after the last incident of a page; the same for both orders of the list."""

    priority: int
    created_at: datetime
    incident_id: UUID

    def encode(self) -> str:
        raw = f"{self.priority}|{self.created_at.isoformat()}|{self.incident_id}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "IncidentCursor":
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            priority, created_at, incident_id = raw.split("|")
            cursor = cls(int(priority), datetime.fromisoformat(created_at), UUID(incident_id))
        except (binascii.Error, UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid cursor") from error
        if cursor.created_at.tzinfo is None:
            raise ValueError("invalid cursor")
        return cursor


def is_queue(statuses: frozenset[IncidentStatus] | None) -> bool:
    """Only open incidents are a queue, the urgent and the longest waiting first. Closed ones
    pile up for good, so a list with them goes from the newest: the oldest would hide the rest."""
    if not statuses:
        return False
    return statuses <= OPEN_INCIDENT_STATUSES


# Fines (#121, docs/adr/0027).
FINE_COMMENT_MAX_LENGTH = 1000
FINE_CANCEL_REASON_MAX_LENGTH = 500
MAX_FINE_PHOTOS = 8
FINE_PAGE_SIZE_MAX = 100


class FineStatus(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    # Saved, the charge not tried yet or its outcome unknown.
    PENDING = "pending"
    PAID = "paid"
    # The card declined: a debt, a new reservation waits for it (docs/adr/0024).
    UNPAID = "unpaid"
    CANCELLED = "cancelled"


class FineTransition(StrEnum):
    CHARGE_SUCCEEDED = "charge_succeeded"
    CHARGE_DECLINED = "charge_declined"
    CANCEL = "cancel"


class RefundStatus(StrEnum):
    """What became of the money of a cancelled fine that was paid; stored as is."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    # Declined by the provider: an operator repeats the cancellation to try again.
    FAILED = "failed"


class FineEventAction(StrEnum):
    """A line of a fine's history; the fine itself is never deleted, a cancellation is a line."""

    CHARGED = "charged"
    PAID = "paid"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    REFUND_FAILED = "refund_failed"


_FINE_RULES: dict[FineTransition, tuple[frozenset[FineStatus], FineStatus]] = {
    FineTransition.CHARGE_SUCCEEDED: (
        frozenset({FineStatus.PENDING, FineStatus.UNPAID}),
        FineStatus.PAID,
    ),
    FineTransition.CHARGE_DECLINED: (
        frozenset({FineStatus.PENDING, FineStatus.UNPAID}),
        FineStatus.UNPAID,
    ),
    FineTransition.CANCEL: (
        frozenset({FineStatus.PENDING, FineStatus.UNPAID, FineStatus.PAID}),
        FineStatus.CANCELLED,
    ),
}
OWED_FINE_STATUSES = frozenset({FineStatus.PENDING, FineStatus.UNPAID})


class FineNotFoundError(AppError):
    """Also for another client's fine: its existence is not told."""

    status = HTTPStatus.NOT_FOUND
    slug = "fine-not-found"
    title = "Fine not found"


class FineTransitionNotAllowedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "fine-transition-not-allowed"
    title = "The fine cannot do this in its current status"


class FineReasonRequiredError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "fine-reason-required"
    title = "A comment is required to charge a fine, a reason to cancel one"


class FineTypeNotManualError(AppError):
    """Automatic fines are charged by the system; an operator does not pick them by hand."""

    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "fine-type-not-manual"
    title = "This fine type is charged automatically"


class FineRentalNotFinishedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "fine-rental-not-finished"
    title = "A fine is charged to a rental whose trip has ended"


class FinePhotoLimitError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "fine-photo-limit"
    title = "The fine already has the largest number of photos"


class FinePhotoNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "fine-photo-not-found"
    title = "Photo not found"


def apply_fine_transition(current: FineStatus, transition: FineTransition) -> FineStatus:
    """The only way a fine changes its status."""
    sources, target = _FINE_RULES[transition]
    if current not in sources:
        raise FineTransitionNotAllowedError
    return target


def ensure_reason(text: str | None) -> str:
    if not (text and text.strip()):
        raise FineReasonRequiredError
    return text.strip()


def is_low_battery_end(battery_pct: int | None, threshold_pct: int) -> bool:
    """A trip ended below the threshold earns the automatic fine; without telemetry at the end
    nothing is known, and a client is not fined on a guess. A zero threshold turns it off."""
    return battery_pct is not None and battery_pct < threshold_pct


def fine_charge_key(fine_id: UUID, attempt: int) -> str:
    return f"fine:{fine_id}:charge:{attempt}"


def fine_refund_key(fine_id: UUID, attempt: int) -> str:
    return f"fine:{fine_id}:refund:{attempt}"


@dataclass(frozen=True)
class Fine:
    id: UUID
    rental_id: UUID
    user_id: UUID
    # The catalog version it was charged by; the amount is copied, the version names the type.
    fine_type_version_id: UUID
    amount: int
    status: FineStatus
    # None for an automatic fine.
    comment: str | None
    created_by: UUID | None
    created_at: datetime
    charge_attempts: int = 0
    # When the worker tries the charge or the refund next; None when nothing is left to try.
    next_at: datetime | None = None
    cancel_reason: str | None = None
    cancelled_by: UUID | None = None
    cancelled_at: datetime | None = None
    refund_status: RefundStatus | None = None
    refund_attempts: int = 0

    @property
    def is_automatic(self) -> bool:
        return self.created_by is None


@dataclass(frozen=True)
class FineFilter:
    statuses: frozenset[FineStatus] | None = None
    fine_type_ids: frozenset[UUID] | None = None
    user_id: UUID | None = None
    rental_id: UUID | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


@dataclass(frozen=True)
class FineCursor:
    """Position after the last fine of a page, newest first."""

    created_at: datetime
    fine_id: UUID

    def encode(self) -> str:
        raw = f"{self.created_at.isoformat()}|{self.fine_id}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "FineCursor":
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            created_at, fine_id = raw.split("|")
            cursor = cls(datetime.fromisoformat(created_at), UUID(fine_id))
        except (binascii.Error, UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid cursor") from error
        if cursor.created_at.tzinfo is None:
            raise ValueError("invalid cursor")
        return cursor
