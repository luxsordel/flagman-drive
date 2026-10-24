from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from flagman.core.dates import require_date_string
from flagman.kyc.domain import (
    REJECTION_REASON_MAX_LENGTH,
    DocumentKind,
    KycStatus,
    normalize_rejection_reason,
)

QUEUE_SIZE_DEFAULT = 50
QUEUE_SIZE_MAX = 100
# The real limit is REJECTION_REASON_MAX_LENGTH, checked after trimming; this one only stops a
# huge string before it is trimmed.
_REASON_INPUT_MAX_LENGTH = 2000


class KycDocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    uploaded_at: datetime


class KycApplicationOut(BaseModel):
    """No links here: the client asks for one only when it shows the photo."""

    model_config = ConfigDict(extra="forbid")

    status: KycStatus
    rejection_reason: str | None = Field(
        description="Set when `rejected`: what to fix before submitting again.",
        examples=["The selfie is blurry"],
    )
    submitted_at: datetime | None
    documents: list[KycDocumentOut]
    missing_documents: list[DocumentKind] = Field(
        description="Still to upload before the application can be submitted."
    )


class DocumentUrlOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="Opens the photo without a token; do not store or share it.")
    expires_in: int = Field(description="Seconds the link keeps working.", examples=[300])


class KycQueueItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    first_name: str | None
    last_name: str | None
    submitted_at: datetime


class KycQueueOut(BaseModel):
    """No paging: decided applications leave the queue, so operators work from the top."""

    model_config = ConfigDict(extra="forbid")

    items: list[KycQueueItemOut] = Field(description="The longest waiting first.")
    total: int = Field(description="All applications waiting for a decision.", examples=[7])


class KycReviewOut(BaseModel):
    """An application as the operator sees it; the profile fields are what the documents prove."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: KycStatus
    first_name: str | None
    last_name: str | None
    birth_date: date | None
    age_years: int | None = Field(description="Whole years today; null without a birth date.")
    submitted_at: datetime | None
    decided_at: datetime | None
    rejection_reason: str | None
    license_issued_on: date | None = Field(description="Entered by the operator at approval.")
    documents: list[KycDocumentOut]


class KycApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    license_issued_on: Annotated[date, BeforeValidator(require_date_string)] = Field(
        description=(
            "Read from the license photo; driving experience counts from it. "
            "Not in the future and not before the birth date, "
            "else `urn:flagman:problem:license-issue-date-invalid`."
        ),
        examples=["2015-06-01"],
    )


class KycRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[
        str,
        Field(max_length=_REASON_INPUT_MAX_LENGTH),
        AfterValidator(normalize_rejection_reason),
    ] = Field(
        description=(
            "Shown to the client: what to fix before submitting again. "
            f"At most {REJECTION_REASON_MAX_LENGTH} characters after trimming."
        ),
        examples=["The selfie is blurry"],
    )
