"""Admission rules: which documents a client uploads and how the application moves. No I/O here."""

from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from http import HTTPStatus

from flagman.core.errors import AppError

# docs/03-security.md: links to document photos live at most five minutes.
SIGNED_URL_TTL = timedelta(minutes=5)
REJECTION_REASON_MAX_LENGTH = 500


class DocumentKind(StrEnum):
    """Values are stored as is and are part of the API path: renaming one breaks both."""

    LICENSE_FRONT = "license_front"
    LICENSE_BACK = "license_back"
    PASSPORT = "passport"
    SELFIE_WITH_LICENSE = "selfie_with_license"


REQUIRED_DOCUMENTS = frozenset(DocumentKind)


class KycStatus(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KycLockedError(AppError):
    """Documents of an application under review or already approved stay as they were checked."""

    status = HTTPStatus.CONFLICT
    slug = "kyc-locked"
    title = "The application is under review or approved"


class KycIncompleteError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "kyc-incomplete"
    title = "Not all documents are uploaded"


class KycNotPendingError(AppError):
    """An operator decision on an application that is not waiting for one."""

    status = HTTPStatus.CONFLICT
    slug = "kyc-not-pending"
    title = "The application is not waiting for a decision"


class DocumentNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "kyc-document-not-found"
    title = "Document is not uploaded"


class DocumentTooLargeError(AppError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    slug = "document-too-large"
    title = "Document photo is too large"


class DocumentTypeUnsupportedError(AppError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    slug = "document-type-unsupported"
    title = "Document photo must be a JPEG, PNG or WebP image"


@dataclass(frozen=True)
class KycApplication:
    """A client without an application row is `NOT_SUBMITTED` with nothing uploaded."""

    status: KycStatus = KycStatus.NOT_SUBMITTED
    uploaded: frozenset[DocumentKind] = frozenset()
    rejection_reason: str | None = None

    @property
    def missing(self) -> frozenset[DocumentKind]:
        return REQUIRED_DOCUMENTS - self.uploaded

    def ensure_documents_editable(self) -> None:
        # After a rejection the client replaces only what was wrong; the rest is checked again.
        if self.status not in (KycStatus.NOT_SUBMITTED, KycStatus.REJECTED):
            raise KycLockedError

    def submit(self) -> "KycApplication":
        self.ensure_documents_editable()
        if self.missing:
            # Kinds only, no document data: safe to show to the client.
            names = ", ".join(sorted(self.missing))
            raise KycIncompleteError(f"Missing documents: {names}.")
        return replace(self, status=KycStatus.PENDING, rejection_reason=None)

    def approve(self) -> "KycApplication":
        if self.status is not KycStatus.PENDING:
            raise KycNotPendingError
        return replace(self, status=KycStatus.APPROVED)

    def reject(self, reason: str) -> "KycApplication":
        if self.status is not KycStatus.PENDING:
            raise KycNotPendingError
        reason = reason.strip()
        # The client reads the reason to know what to fix, so it cannot be empty.
        if not reason or len(reason) > REJECTION_REASON_MAX_LENGTH:
            raise ValueError(f"rejection reason must be 1-{REJECTION_REASON_MAX_LENGTH} characters")
        return replace(self, status=KycStatus.REJECTED, rejection_reason=reason)
