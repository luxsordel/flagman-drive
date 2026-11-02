"""Support rules: conversation and incident statuses, limits and who may do what (docs/adr/0023)."""

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
