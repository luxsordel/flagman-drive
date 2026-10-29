from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from flagman.rentals.domain import RentalStatus
from flagman.support.domain import (
    INCIDENT_COMMENT_MAX_LENGTH,
    INCIDENT_DESCRIPTION_MAX_LENGTH,
    MESSAGE_TEXT_MAX_LENGTH,
    QUEUE_SIZE_MAX,
    SEQ_MAX,
    ConversationStatus,
    IncidentCursor,
    IncidentEventAction,
    IncidentStatus,
    IncidentType,
    MessageAuthor,
    MessageKind,
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
    unread: int = Field(description="Client messages no staff member has read yet.")
    first_name: str | None
    last_name: str | None
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


class IncidentCommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: IncidentComment


class IncidentPhotoOut(BaseModel):
    id: UUID
    uploaded_at: datetime


class IncidentEventOut(BaseModel):
    action: IncidentEventAction
    staff_id: UUID | None
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
