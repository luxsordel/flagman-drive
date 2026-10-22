from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from flagman.kyc.domain import DocumentKind, KycStatus


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
