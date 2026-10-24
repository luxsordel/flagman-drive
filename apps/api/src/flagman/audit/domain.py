"""What an audit event may contain. No I/O here."""

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100

DETAILS_MAX_KEYS = 20
DETAILS_MAX_STRING_LENGTH = 500

_DETAILS_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
# A safety net, not a guarantee: callers must not pass personal data at all. These catch the
# usual slips - a phone in E.164 or as a bare digit run, and an email address.
_PHONE_LIKE = re.compile(r"\+\d{6,}|\d{9,}")
_EMAIL_LIKE = re.compile(r"[^\s@]+@[^\s@]+")


class ActorType(StrEnum):
    STAFF = "staff"
    CLIENT = "client"
    SYSTEM = "system"


class AuditAction(StrEnum):
    """Every recorded action is listed here, so filtering by action is reliable.

    Values are stored as is: renaming one breaks filtering of older events.
    """

    STAFF_CREATED = "staff.created"
    STAFF_SIGNED_IN = "staff.signed_in"
    STAFF_SIGN_IN_FAILED = "staff.sign_in_failed"
    STAFF_SIGNED_OUT = "staff.signed_out"
    STAFF_TOTP_ENROLLMENT_STARTED = "staff.totp_enrollment_started"
    STAFF_TOTP_ENROLLED = "staff.totp_enrolled"
    STAFF_TOTP_RESET = "staff.totp_reset"
    STAFF_PASSWORD_CHANGED = "staff.password_changed"  # noqa: S105 - an event name, not a secret
    STAFF_DISABLED = "staff.disabled"
    KYC_APPROVED = "kyc.approved"
    KYC_REJECTED = "kyc.rejected"
    KYC_DOCUMENT_VIEWED = "kyc.document_viewed"


class AuditTargetType(StrEnum):
    """Every kind of object an event names, so the admin panel can label each one.

    Values are stored as is, like AuditAction: renaming one leaves older events with the old name.
    """

    STAFF_MEMBER = "staff_member"
    KYC_APPLICATION = "kyc_application"


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.type is ActorType.SYSTEM) != (self.id is None):
            raise ValueError("a system actor has no id, any other actor must have one")


SYSTEM = Actor(ActorType.SYSTEM)


@dataclass(frozen=True)
class AuditTarget:
    """The object the action was applied to, e.g. a KYC application or a staff member."""

    type: AuditTargetType
    id: UUID


@dataclass(frozen=True)
class RecordedTarget:
    # A plain string, as AuditEventRecord.action: an event outlives changes to AuditTargetType.
    type: str
    id: UUID


@dataclass(frozen=True)
class RequestOrigin:
    """Where the action came from. Background jobs use `RequestOrigin()`."""

    ip_hash: str | None = None
    request_id: UUID | None = None


DetailValue = str | int | bool | UUID | None


@dataclass(frozen=True)
class AuditEntry:
    """One event as a module records it; the time is set by the database."""

    actor: Actor
    action: AuditAction
    origin: RequestOrigin
    target: AuditTarget | None = None
    # Ids only, never personal data: see validate_details.
    details: Mapping[str, DetailValue] = field(default_factory=dict)


def validate_details(details: Mapping[str, DetailValue]) -> dict[str, str | int | bool | None]:
    """Checks the details and returns them ready for JSON.

    Ids go in as UUID objects: a string value that looks like a phone number or an email is
    rejected, and the last group of a UUID may be twelve digits in a row. So is an integer of
    nine digits or more - a phone number passed as a number.
    """
    if len(details) > DETAILS_MAX_KEYS:
        raise ValueError(f"audit details take at most {DETAILS_MAX_KEYS} keys")
    checked: dict[str, str | int | bool | None] = {}
    for key, value in details.items():
        # fullmatch: with match, "$" also accepts a trailing newline.
        if not _DETAILS_KEY.fullmatch(key):
            raise ValueError(f"audit detail key must be snake_case: {key!r}")
        if isinstance(value, UUID):
            checked[key] = str(value)
        elif isinstance(value, str):
            _check_string(key, value)
            checked[key] = value
        elif isinstance(value, bool) or value is None:
            checked[key] = value
        elif isinstance(value, int):
            if _PHONE_LIKE.search(str(value)):
                raise ValueError(f"audit detail {key!r} looks like a phone number")
            checked[key] = value
        else:
            raise TypeError(f"audit detail {key!r} must be str, int, bool, UUID or None")
    return checked


def _check_string(key: str, value: str) -> None:
    # The value itself stays out of the messages: it is exactly what must not be leaked.
    if len(value) > DETAILS_MAX_STRING_LENGTH:
        raise ValueError(f"audit detail {key!r} is longer than {DETAILS_MAX_STRING_LENGTH}")
    if _PHONE_LIKE.search(value) or _EMAIL_LIKE.search(value):
        raise ValueError(f"audit detail {key!r} looks like a phone number or an email")


@dataclass(frozen=True)
class AuditEventRecord:
    id: UUID
    occurred_at: datetime
    actor: Actor
    # A plain string: an event outlives changes to AuditAction.
    action: str
    target: RecordedTarget | None
    origin: RequestOrigin
    details: Mapping[str, str | int | bool | None]


@dataclass(frozen=True)
class AuditEventFilter:
    """`occurred_from` is inclusive and `occurred_to` exclusive, so periods chain without gaps."""

    actor_id: UUID | None = None
    action: AuditAction | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


@dataclass(frozen=True)
class Cursor:
    """Position after the last event of a page; pages go from the newest event to the oldest."""

    occurred_at: datetime
    event_id: UUID

    def encode(self) -> str:
        raw = f"{self.occurred_at.isoformat()}|{self.event_id}"
        return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> "Cursor":
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            occurred_at, event_id = raw.split("|")
            cursor = cls(datetime.fromisoformat(occurred_at), UUID(event_id))
        except (binascii.Error, UnicodeDecodeError, ValueError) as error:
            raise ValueError("invalid cursor") from error
        if cursor.occurred_at.tzinfo is None:
            raise ValueError("invalid cursor")
        return cursor
