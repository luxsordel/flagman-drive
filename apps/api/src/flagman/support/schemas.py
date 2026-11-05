from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from flagman.rentals.domain import RentalStatus
from flagman.support.domain import (
    FINE_CANCEL_REASON_MAX_LENGTH,
    FINE_COMMENT_MAX_LENGTH,
    FINE_PAGE_SIZE_MAX,
    INCIDENT_COMMENT_MAX_LENGTH,
    INCIDENT_DESCRIPTION_MAX_LENGTH,
    MESSAGE_TEXT_MAX_LENGTH,
    QUEUE_SIZE_MAX,
    SEQ_MAX,
    ConversationStatus,
    FineCursor,
    FineEventAction,
    FineStatus,
    IncidentCursor,
    IncidentEventAction,
    IncidentStatus,
    IncidentType,
    MessageAuthor,
    MessageKind,
    RefundStatus,
)

MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MESSAGE_TEXT_MAX_LENGTH)
]
IncidentDescription = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=INCIDENT_DESCRIPTION_MAX_LENGTH
    ),
]
IncidentComment = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=INCIDENT_COMMENT_MAX_LENGTH),
]
INCIDENT_PAGE_SIZE_DEFAULT = 50
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: MessageText
    rental_id: UUID | None = Field(
        default=None,
        description=(
            "Attaches the conversation to the client's rental, an open one too: the latest "
            "rental the client wrote from is the one the operator sees."
        ),
    )
    client_message_id: UUID | None = Field(
        default=None,
        description=(
            "Chosen by the app once per message: sending it again returns the message already "
            "saved instead of a duplicate."
        ),
    )


class StaffMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: MessageText
    seen_seq: int = Field(
        ge=0,
        le=SEQ_MAX,
        description=(
            "The largest `seq` the operator has been shown: client messages after it stay unread."
        ),
    )


class MessageOut(BaseModel):
    id: UUID
    seq: int = Field(description="Pass the largest one seen as `after` to poll for newer ones.")
    author: MessageAuthor
    staff_name: str | None = Field(
        description='The operator who answered, shown after "FLAGMAN Support"; None if unnamed.'
    )
    kind: MessageKind
    text: str | None
    created_at: datetime


class StaffMessageOut(MessageOut):
    staff_id: UUID | None = Field(description="Who answered; the client is never told.")


class MessagePageOut(BaseModel):
    conversation_id: UUID | None = Field(description="None until the client writes first.")
    status: ConversationStatus | None
    items: list[MessageOut]


class StaffMessagePageOut(BaseModel):
    conversation_id: UUID
    status: ConversationStatus
    items: list[StaffMessageOut]


class ConversationOut(BaseModel):
    id: UUID
    user_id: UUID
    rental_id: UUID | None
    rental_status: RentalStatus | None
    car_id: UUID | None
    car_plate: str | None
    status: ConversationStatus
    assigned_staff_id: UUID | None
    assigned_staff_name: str | None
    unread: int = Field(description="Client messages no staff member has read yet.")
    first_name: str | None
    last_name: str | None
    last_message_text: str | None = Field(description="None when the last message is a photo.")
    created_at: datetime
    last_message_at: datetime


class ConversationQueueOut(BaseModel):
    items: list[ConversationOut]


class UnreadOut(BaseModel):
    conversations: int = Field(description="Open conversations with unread client messages.")
    incidents: int = Field(description="Incidents nobody has taken yet.")


class ClientUnreadOut(BaseModel):
    messages: int


class PhotoUrlOut(BaseModel):
    url: str = Field(description="Opens the photo without a token; do not store or share it.")
    expires_in: int = Field(description="Seconds the link keeps working.", examples=[300])


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: Latitude
    lon: Longitude


class IncidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IncidentType
    description: IncidentDescription
    location: LocationIn | None = None
    rental_id: UUID | None = Field(
        default=None,
        description=(
            "A finished rental of the client the report is about; an active one is attached "
            "without it and wins."
        ),
    )
    client_report_id: UUID | None = Field(
        default=None,
        description=(
            "Chosen by the app once per report: sending it again returns the incident already "
            "saved instead of a duplicate."
        ),
    )


class IncidentCommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: IncidentComment


class IncidentPhotoOut(BaseModel):
    id: UUID
    uploaded_at: datetime


class IncidentEventOut(BaseModel):
    action: IncidentEventAction
    staff_id: UUID | None
    staff_name: str | None
    comment: str | None
    created_at: datetime


class LocationOut(BaseModel):
    lat: float
    lon: float


class IncidentOut(BaseModel):
    id: UUID
    type: IncidentType
    status: IncidentStatus
    description: str
    location: LocationOut | None
    rental_id: UUID | None
    car_id: UUID | None
    photos: list[IncidentPhotoOut]
    created_at: datetime
    updated_at: datetime


class StaffIncidentOut(IncidentOut):
    user_id: UUID
    first_name: str | None
    last_name: str | None
    priority: int = Field(description="0 for an accident during a rental, 1 otherwise.")
    assigned_staff_id: UUID | None
    assigned_staff_name: str | None
    rental_status: RentalStatus | None
    car_plate: str | None
    sent_to_service: bool
    events: list[IncidentEventOut]


class IncidentPageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor: str | None = Field(
        default=None, max_length=200, description="`next_cursor` of the previous page."
    )
    limit: int = Field(default=INCIDENT_PAGE_SIZE_DEFAULT, ge=1, le=QUEUE_SIZE_MAX)

    @field_validator("cursor")
    @classmethod
    def _check_cursor(cls, value: str | None) -> str | None:
        # A broken cursor is the caller's mistake: 422 here, not 500 in the service.
        if value is not None:
            IncidentCursor.decode(value)
        return value


class StaffIncidentQuery(IncidentPageQuery):
    status: list[IncidentStatus] | None = Field(
        default=None,
        description=(
            "Only `new` and `in_progress` make the operators' queue: accidents during a trip "
            "first, then the longest waiting. Any other list goes from the newest."
        ),
    )
    type: IncidentType | None = None


class IncidentListOut(BaseModel):
    items: list[IncidentOut]
    next_cursor: str | None = Field(description="Absent on the last page.")


class StaffIncidentListOut(BaseModel):
    items: list[StaffIncidentOut]
    next_cursor: str | None = Field(description="Absent on the last page.")


# Fines (docs/adr/0028)

FineComment = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FINE_COMMENT_MAX_LENGTH)
]
FineCancelReason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=FINE_CANCEL_REASON_MAX_LENGTH
    ),
]
FINE_PAGE_SIZE_DEFAULT = 50


class FineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rental_id: UUID = Field(description="A rental whose trip has ended.")
    fine_type_id: UUID = Field(description="A manual type of the catalog.")
    comment: FineComment = Field(description="What happened; the client sees it.")


class FineCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: FineCancelReason = Field(description="Why; the client sees it.")


class FinePhotoOut(BaseModel):
    id: UUID
    uploaded_at: datetime


class FineOut(BaseModel):
    """A fine as the client sees it."""

    id: UUID
    rental_id: UUID
    fine_type_id: UUID
    code: str
    name_ru: str
    name_en: str
    amount: int = Field(description="Kopecks of BYN.")
    automatic: bool = Field(description="Charged by the system, not by an operator.")
    status: FineStatus
    comment: str | None = Field(description="The operator's; none for an automatic fine.")
    cancel_reason: str | None
    refund_status: RefundStatus | None = Field(
        description="Only for a cancelled fine that was paid: where its money stands."
    )
    photos: list[FinePhotoOut]
    created_at: datetime
    cancelled_at: datetime | None


class FineEventOut(BaseModel):
    action: FineEventAction
    staff_id: UUID | None = Field(description="None for what the system did on its own.")
    staff_name: str | None
    comment: str | None
    created_at: datetime


class StaffFineOut(FineOut):
    user_id: UUID
    first_name: str | None
    last_name: str | None
    car_plate: str | None
    created_by: UUID | None
    created_by_name: str | None
    cancelled_by: UUID | None
    events: list[FineEventOut]


class FineListOut(BaseModel):
    items: list[FineOut]
    next_cursor: str | None = Field(default=None, description="Absent on the last page.")


class StaffFineQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor: str | None = Field(
        default=None, max_length=200, description="`next_cursor` of the previous page."
    )
    limit: int = Field(default=FINE_PAGE_SIZE_DEFAULT, ge=1, le=FINE_PAGE_SIZE_MAX)
    status: list[FineStatus] | None = None
    fine_type_id: list[UUID] | None = None
    user_id: UUID | None = None
    rental_id: UUID | None = None
    created_from: AwareDatetime | None = Field(default=None, description="Inclusive.")
    created_to: AwareDatetime | None = Field(default=None, description="Exclusive.")

    @field_validator("cursor")
    @classmethod
    def _check_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            FineCursor.decode(value)
        return value


class StaffFineListOut(BaseModel):
    items: list[StaffFineOut]
    next_cursor: str | None = Field(description="Absent on the last page.")
