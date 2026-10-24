"""The admission: the client's documents and the operators' review of them.

Other modules use only `get_admission_status` and `lock_personal_data`.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    RequestOrigin,
)
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.identity import service as identity
from flagman.kyc import repository
from flagman.kyc.domain import (
    SIGNED_URL_TTL,
    AdmissionRules,
    Applicant,
    ApplicationNotFoundError,
    DocumentKind,
    DocumentNotFoundError,
    KycApplication,
    KycStatus,
    full_years,
)
from flagman.kyc.images import OUTPUT_CONTENT_TYPE, clean_document_photo
from flagman.kyc.ports import DocumentStorage
from flagman.kyc.repository import ApplicationRecord, StoredPhoto
from flagman.kyc.schemas import (
    DocumentUrlOut,
    KycApplicationOut,
    KycDocumentOut,
    KycQueueItemOut,
    KycQueueOut,
    KycReviewOut,
)

logger = structlog.get_logger(__name__)

_HOUR = 3600
# Four documents and a few retakes each; the limit stops a client from filling the storage.
UPLOAD_LIMIT = Limit("kyc-upload-user", 30, _HOUR)
# A link is asked for every time a photo is shown; the limit caps how many links can leak.
DOCUMENT_URL_LIMIT = Limit("kyc-document-url-user", 120, _HOUR)
# An operator opens four photos per application, some more than once: room for a busy hour of
# reviews, and still far from paging through every client's documents.
STAFF_DOCUMENT_URL_LIMIT = Limit("kyc-document-url-staff", 600, _HOUR)

# The audit log names the application, not the client: the id leads to the documents only for
# those allowed to see them.
_APPLICATION_TARGET = AuditTargetType.KYC_APPLICATION


async def get_admission_status(db: AsyncSession, user_id: UUID) -> KycStatus:
    """Only `APPROVED` admits the client to rentals (docs/03-security.md).

    Joins the caller's transaction if one is open, so a rental can check the status and start
    in one transaction.
    """
    if db.in_transaction():
        return await repository.get_status(db, user_id)
    async with db.begin():
        return await repository.get_status(db, user_id)


async def lock_personal_data(db: AsyncSession, user_id: UUID) -> None:
    """Refuses a change of the client's name or birth date while under review or approved.

    The operator checks the documents and the age against them. Runs in the caller's
    transaction and holds the application row until it ends, so an operator decision on this
    client waits for the change and then sees it.
    """
    status = await repository.get_status(db, user_id, for_share=True)
    KycApplication(status=status).ensure_editable()


@dataclass(frozen=True)
class KycService:
    db: AsyncSession
    limiter: RateLimiter

    async def get_application(self, user_id: UUID) -> KycApplicationOut:
        async with self.db.begin():
            record = await repository.get_application(self.db, user_id)
        return _to_out(record)

    async def count_upload(self, user_id: UUID) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(UPLOAD_LIMIT, str(user_id))

    async def upload_document(
        self,
        storage: DocumentStorage,
        user_id: UUID,
        kind: DocumentKind,
        data: bytes,
        now: datetime,
    ) -> KycApplicationOut:
        photo = await asyncio.to_thread(clean_document_photo, data)

        async with self.db.begin():
            await repository.ensure_application(self.db, user_id, now)
            record = await repository.get_application(self.db, user_id)
            if record is None:
                raise RuntimeError("application vanished right after it was created")
            # Checked before the photo goes to the storage, and again under the lock below.
            record.state.ensure_editable()
        # A new key for every upload: a link handed out for the old photo never shows the new one.
        key = f"kyc/{record.id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)

        try:
            async with self.db.begin():
                record = await repository.get_application(self.db, user_id, for_update=True)
                if record is None:
                    raise RuntimeError("application vanished during the upload")
                # The client may have submitted from another device while the photo was uploading.
                record.state.ensure_editable()
                replaced = record.document(kind)
                await repository.save_document(
                    self.db, record.id, StoredPhoto(kind, key, len(photo)), now
                )
                record = await repository.get_application(self.db, user_id)
        except Exception:
            await _delete_quietly(storage, key, kind)
            raise
        if replaced is not None:
            await _delete_quietly(storage, replaced.object_key, kind)
        # Ids and the kind only: the key and anything about the photo stay out of the log.
        logger.info("kyc_document_uploaded", user_id=str(user_id), kind=kind)
        return _to_out(record)

    async def document_url(
        self, storage: DocumentStorage, user_id: UUID, kind: DocumentKind
    ) -> DocumentUrlOut:
        await self.limiter.hit(DOCUMENT_URL_LIMIT, str(user_id))
        async with self.db.begin():
            record = await repository.get_application(self.db, user_id)
        document = record.document(kind) if record else None
        if document is None:
            raise DocumentNotFoundError
        url = await storage.signed_get_url(document.object_key, SIGNED_URL_TTL)
        return DocumentUrlOut(url=url, expires_in=int(SIGNED_URL_TTL.total_seconds()))

    async def submit(self, user_id: UUID, now: datetime) -> KycApplicationOut:
        async with self.db.begin():
            record = await repository.get_application(self.db, user_id, for_update=True)
            # Raises for a missing application too: then every document is missing.
            (record.state if record else KycApplication()).submit()
            if record is None:
                raise RuntimeError("an application without documents passed the submission")
            await repository.set_submitted(self.db, record.id, now)
            record = await repository.get_application(self.db, user_id)
        logger.info("kyc_application_submitted", user_id=str(user_id))
        return _to_out(record)


@dataclass(frozen=True)
class KycReviewService:
    """The operators' side; the router lets only `operator` and `admin` reach it."""

    db: AsyncSession
    limiter: RateLimiter
    rules: AdmissionRules
    # Every decision and every link handed out goes to the audit log with it.
    origin: RequestOrigin

    async def queue(self, limit: int) -> KycQueueOut:
        async with self.db.begin():
            pending = await repository.list_pending(self.db, limit)
            total = await repository.count_pending(self.db)
            people = await identity.get_client_identities(
                self.db, [item.user_id for item in pending]
            )
        items = []
        for item in pending:
            person = people.get(item.user_id)
            items.append(
                KycQueueItemOut(
                    id=item.id,
                    first_name=person.first_name if person else None,
                    last_name=person.last_name if person else None,
                    submitted_at=item.submitted_at,
                )
            )
        return KycQueueOut(items=items, total=total)

    async def get(self, application_id: UUID, today: date) -> KycReviewOut:
        async with self.db.begin():
            record = await self._get(application_id)
            applicant = await self._applicant(record.user_id)
        return _to_review(record, applicant, today)

    async def document_url(
        self,
        storage: DocumentStorage,
        staff_id: UUID,
        application_id: UUID,
        kind: DocumentKind,
    ) -> DocumentUrlOut:
        await self.limiter.hit(STAFF_DOCUMENT_URL_LIMIT, str(staff_id))
        async with self.db.begin():
            record = await self._get(application_id)
            document = record.document(kind)
            if document is None:
                raise DocumentNotFoundError
            # Who looked at whose documents; recorded before the link exists.
            await audit.record(
                self.db,
                AuditEntry(
                    Actor(ActorType.STAFF, staff_id),
                    AuditAction.KYC_DOCUMENT_VIEWED,
                    self.origin,
                    AuditTarget(_APPLICATION_TARGET, application_id),
                    details={"kind": kind.value},
                ),
            )
        url = await storage.signed_get_url(document.object_key, SIGNED_URL_TTL)
        return DocumentUrlOut(url=url, expires_in=int(SIGNED_URL_TTL.total_seconds()))

    async def approve(
        self,
        staff_id: UUID,
        application_id: UUID,
        license_issued_on: date,
        now: datetime,
    ) -> KycReviewOut:
        # The UTC date rather than Minsk's: for three hours a day the client counts a day
        # younger, which can only delay an approval, never grant one early.
        today = now.date()
        async with self.db.begin():
            record = await self._get(application_id, for_update=True)
            applicant = await self._applicant(record.user_id)
            decided = record.state.approve(applicant, license_issued_on, self.rules, today)
            await self._decide(staff_id, record.id, decided, AuditAction.KYC_APPROVED, now)
            record = await self._get(application_id)
        logger.info("kyc_application_approved", application_id=str(application_id))
        return _to_review(record, applicant, today)

    async def reject(
        self,
        staff_id: UUID,
        application_id: UUID,
        reason: str,
        now: datetime,
    ) -> KycReviewOut:
        async with self.db.begin():
            record = await self._get(application_id, for_update=True)
            decided = record.state.reject(reason)
            # The reason stays out of the audit log: free text may carry personal data.
            await self._decide(staff_id, record.id, decided, AuditAction.KYC_REJECTED, now)
            record = await self._get(application_id)
            applicant = await self._applicant(record.user_id)
        logger.info("kyc_application_rejected", application_id=str(application_id))
        return _to_review(record, applicant, now.date())

    async def _get(self, application_id: UUID, *, for_update: bool = False) -> ApplicationRecord:
        record = await repository.get_application_by_id(
            self.db, application_id, for_update=for_update
        )
        if record is None:
            raise ApplicationNotFoundError
        return record

    async def _applicant(self, user_id: UUID) -> Applicant:
        person = (await identity.get_client_identities(self.db, [user_id])).get(user_id)
        if person is None:
            # The foreign key keeps the client while the application exists.
            raise RuntimeError("an application without its client")
        return Applicant(person.first_name, person.last_name, person.birth_date)

    async def _decide(
        self,
        staff_id: UUID,
        application_id: UUID,
        decided: KycApplication,
        action: AuditAction,
        now: datetime,
    ) -> None:
        await repository.set_decision(self.db, application_id, decided, now)
        await audit.record(
            self.db,
            AuditEntry(
                Actor(ActorType.STAFF, staff_id),
                action,
                self.origin,
                AuditTarget(_APPLICATION_TARGET, application_id),
            ),
        )


async def _delete_quietly(storage: DocumentStorage, key: str, kind: DocumentKind) -> None:
    """A photo left behind is only wasted space; failing the request for it would be worse."""
    try:
        await storage.delete(key)
    except Exception as error:
        logger.warning("kyc_object_delete_failed", kind=kind, error=type(error).__name__)


def _to_out(record: ApplicationRecord | None) -> KycApplicationOut:
    state = record.state if record else KycApplication()
    return KycApplicationOut(
        status=state.status,
        rejection_reason=state.rejection_reason,
        submitted_at=record.submitted_at if record else None,
        documents=[
            KycDocumentOut(kind=document.kind, uploaded_at=document.uploaded_at)
            for document in (record.documents if record else ())
        ],
        missing_documents=sorted(state.missing),
    )


def _to_review(record: ApplicationRecord, applicant: Applicant, today: date) -> KycReviewOut:
    birth_date = applicant.birth_date
    return KycReviewOut(
        id=record.id,
        status=record.status,
        first_name=applicant.first_name,
        last_name=applicant.last_name,
        birth_date=birth_date,
        age_years=full_years(birth_date, today) if birth_date is not None else None,
        submitted_at=record.submitted_at,
        decided_at=record.decided_at,
        rejection_reason=record.rejection_reason,
        license_issued_on=record.license_issued_on,
        documents=[
            KycDocumentOut(kind=document.kind, uploaded_at=document.uploaded_at)
            for document in record.documents
        ],
    )
