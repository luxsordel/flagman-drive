"""Admission rules: which documents a client uploads and how the application moves. No I/O here."""

from dataclasses import dataclass, replace
from datetime import date, timedelta
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


class KycProfileIncompleteError(AppError):
    """The operator checks the documents against the profile, so it must be filled in first.

    Raised on submission, and on approval of an application submitted before that check (#114).
    """

    status = HTTPStatus.CONFLICT
    slug = "kyc-profile-incomplete"
    title = "The client has not filled in the name and birth date"


class KycRequirementsNotMetError(AppError):
    """Approval refused by the admission rules; the operator rejects with a reason instead."""

    status = HTTPStatus.CONFLICT
    slug = "kyc-requirements-not-met"
    title = "The client does not meet the admission requirements"


class LicenseIssueDateInvalidError(AppError):
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "license-issue-date-invalid"
    title = "Invalid license issue date"


class ApplicationNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "kyc-application-not-found"
    title = "Application not found"


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


def normalize_rejection_reason(reason: str) -> str:
    """The client reads the reason to know what to fix, so it cannot be empty.

    One rule for the request schema and the domain: Python's `strip` also drops control
    characters such as U+001F that pydantic's own whitespace trimming keeps.
    """
    reason = reason.strip()
    if not reason or len(reason) > REJECTION_REASON_MAX_LENGTH:
        raise ValueError(f"rejection reason must be 1-{REJECTION_REASON_MAX_LENGTH} characters")
    return reason


def full_years(since: date, today: date) -> int:
    """Whole years from `since` to `today`; a 29 February birthday counts from 1 March."""
    years = today.year - since.year
    if (today.month, today.day) < (since.month, since.day):
        years -= 1
    return years


@dataclass(frozen=True)
class AdmissionRules:
    """Thresholds from the settings (docs/01-vision-and-mvp.md: 21 years of age, 2 of driving)."""

    min_age_years: int
    min_driving_years: int


@dataclass(frozen=True)
class Applicant:
    """What the operator checks the documents against; taken from the client's profile."""

    first_name: str | None
    last_name: str | None
    birth_date: date | None

    @property
    def is_complete(self) -> bool:
        return bool(self.first_name and self.last_name and self.birth_date is not None)


@dataclass(frozen=True)
class KycApplication:
    """A client without an application row is `NOT_SUBMITTED` with nothing uploaded."""

    status: KycStatus = KycStatus.NOT_SUBMITTED
    uploaded: frozenset[DocumentKind] = frozenset()
    rejection_reason: str | None = None
    license_issued_on: date | None = None

    @property
    def missing(self) -> frozenset[DocumentKind]:
        return REQUIRED_DOCUMENTS - self.uploaded

    def ensure_editable(self) -> None:
        """Documents and the profile data they are checked against change only before review.

        After a rejection the client replaces only what was wrong; the rest is checked again.
        """
        if self.status not in (KycStatus.NOT_SUBMITTED, KycStatus.REJECTED):
            raise KycLockedError

    def submit(self, applicant: Applicant) -> "KycApplication":
        self.ensure_editable()
        if self.missing:
            # Kinds only, no document data: safe to show to the client.
            names = ", ".join(sorted(self.missing))
            raise KycIncompleteError(f"Missing documents: {names}.")
        # Once under review the name and birth date are locked, so an empty profile could
        # neither be approved nor filled in.
        if not applicant.is_complete:
            raise KycProfileIncompleteError
        return replace(self, status=KycStatus.PENDING, rejection_reason=None)

    def approve(
        self, applicant: Applicant, license_issued_on: date, rules: AdmissionRules, today: date
    ) -> "KycApplication":
        """`license_issued_on` is read by the operator from the license photo."""
        if self.status is not KycStatus.PENDING:
            raise KycNotPendingError
        birth_date = applicant.birth_date
        if not applicant.is_complete or birth_date is None:
            raise KycProfileIncompleteError
        if license_issued_on > today:
            raise LicenseIssueDateInvalidError("The license issue date must not be in the future.")
        if license_issued_on < birth_date:
            raise LicenseIssueDateInvalidError(
                "The license issue date must not be before the birth date."
            )
        # Only the thresholds go into the details, never the client's dates or age.
        if full_years(birth_date, today) < rules.min_age_years:
            raise KycRequirementsNotMetError(
                f"The client must be at least {rules.min_age_years} years old."
            )
        if full_years(license_issued_on, today) < rules.min_driving_years:
            raise KycRequirementsNotMetError(
                f"The client must have held a license for at least {rules.min_driving_years} years."
            )
        return replace(self, status=KycStatus.APPROVED, license_issued_on=license_issued_on)

    def reject(self, reason: str) -> "KycApplication":
        if self.status is not KycStatus.PENDING:
            raise KycNotPendingError
        return replace(
            self, status=KycStatus.REJECTED, rejection_reason=normalize_rejection_reason(reason)
        )
