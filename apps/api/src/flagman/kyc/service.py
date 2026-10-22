"""Client side of the admission. Other modules use only `get_admission_status`."""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.core.ratelimit import Limit, RateLimiter
from flagman.kyc import repository
from flagman.kyc.domain import (
    SIGNED_URL_TTL,
    DocumentKind,
    DocumentNotFoundError,
    KycApplication,
    KycStatus,
)
from flagman.kyc.images import OUTPUT_CONTENT_TYPE, clean_document_photo
from flagman.kyc.ports import DocumentStorage
from flagman.kyc.repository import ApplicationRecord, StoredPhoto
from flagman.kyc.schemas import DocumentUrlOut, KycApplicationOut, KycDocumentOut

logger = structlog.get_logger(__name__)

_HOUR = 3600
# Four documents and a few retakes each; the limit stops a client from filling the storage.
UPLOAD_LIMIT = Limit("kyc-upload-user", 30, _HOUR)
# A link is asked for every time a photo is shown; the limit caps how many links can leak.
DOCUMENT_URL_LIMIT = Limit("kyc-document-url-user", 120, _HOUR)


async def get_admission_status(db: AsyncSession, user_id: UUID) -> KycStatus:
    """Only `APPROVED` admits the client to rentals (docs/03-security.md).

    Joins the caller's transaction if one is open, so a rental can check the status and start
    in one transaction.
    """
    if db.in_transaction():
        return await repository.get_status(db, user_id)
    async with db.begin():
        return await repository.get_status(db, user_id)


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
            record.state.ensure_documents_editable()
        # A new key for every upload: a link handed out for the old photo never shows the new one.
        key = f"kyc/{record.id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)

        try:
            async with self.db.begin():
                record = await repository.get_application(self.db, user_id, for_update=True)
                if record is None:
                    raise RuntimeError("application vanished during the upload")
                # The client may have submitted from another device while the photo was uploading.
                record.state.ensure_documents_editable()
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
