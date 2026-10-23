from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from flagman.audit.domain import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, ActorType, AuditAction, Cursor


class AuditEventQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: UUID | None = Field(default=None, description="Only events of this staff member.")
    action: AuditAction | None = None
    # Aware only: a time without an offset would silently mean different moments.
    occurred_from: AwareDatetime | None = Field(default=None, description="Inclusive.")
    occurred_to: AwareDatetime | None = Field(default=None, description="Exclusive.")
    cursor: str | None = Field(
        default=None, max_length=200, description="`next_cursor` of the previous page."
    )
    limit: int = Field(default=PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX)

    @field_validator("cursor")
    @classmethod
    def _check_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            Cursor.decode(value)
        return value

    @model_validator(mode="after")
    def _check_period(self) -> "AuditEventQuery":
        # An empty period is a mistake in the query, not a period without events.
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from >= self.occurred_to
        ):
            raise ValueError("occurred_from must be earlier than occurred_to")
        return self


class AuditEventTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(examples=["kyc_document"])
    id: UUID


class AuditEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    occurred_at: datetime
    actor_type: ActorType
    actor_id: UUID | None = Field(description="Absent for `system`.")
    action: str = Field(examples=[AuditAction.STAFF_SIGNED_IN])
    target: AuditEventTarget | None
    ip_hash: str | None = Field(
        description="Keyed hash of the client IP: equal values mean the same address."
    )
    request_id: UUID | None
    details: dict[str, str | int | bool | None]


class AuditEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AuditEventOut]
    next_cursor: str | None = Field(description="Absent on the last page.")
