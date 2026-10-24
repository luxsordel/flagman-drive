from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.kyc.domain import DocumentKind, KycApplication, KycStatus
from flagman.kyc.models import Application, Document


@dataclass(frozen=True)
class DocumentRecord:
    kind: DocumentKind
    object_key: str
    uploaded_at: datetime


@dataclass(frozen=True)
class ApplicationRecord:
    id: UUID
    user_id: UUID
    status: KycStatus
    rejection_reason: str | None
    license_issued_on: date | None
    submitted_at: datetime | None
    decided_at: datetime | None
    documents: tuple[DocumentRecord, ...]

    @property
    def state(self) -> KycApplication:
        return KycApplication(
            status=self.status,
            uploaded=frozenset(document.kind for document in self.documents),
            rejection_reason=self.rejection_reason,
            license_issued_on=self.license_issued_on,
        )

    def document(self, kind: DocumentKind) -> DocumentRecord | None:
        return next((document for document in self.documents if document.kind is kind), None)


async def get_application(
    db: AsyncSession, user_id: UUID, *, for_update: bool = False
) -> ApplicationRecord | None:
    query = select(Application).where(Application.user_id == user_id)
    if for_update:
        # Uploads and the submission of one client take turns, so a photo cannot be replaced
        # between the completeness check and the status change.
        query = query.with_for_update()
    return await _load(db, await db.scalar(query))


async def get_application_by_id(
    db: AsyncSession, application_id: UUID, *, for_update: bool = False
) -> ApplicationRecord | None:
    query = select(Application).where(Application.id == application_id)
    if for_update:
        # A decision waits for an upload, a submission or a profile change of the same client.
        query = query.with_for_update()
    return await _load(db, await db.scalar(query))


async def _load(db: AsyncSession, application: Application | None) -> ApplicationRecord | None:
    if application is None:
        return None
    rows = await db.scalars(
        select(Document).where(Document.application_id == application.id).order_by(Document.kind)
    )
    return ApplicationRecord(
        id=application.id,
        user_id=application.user_id,
        status=KycStatus(application.status),
        rejection_reason=application.rejection_reason,
        license_issued_on=application.license_issued_on,
        submitted_at=application.submitted_at,
        decided_at=application.decided_at,
        documents=tuple(
            DocumentRecord(DocumentKind(row.kind), row.object_key, row.uploaded_at) for row in rows
        ),
    )


async def get_status(db: AsyncSession, user_id: UUID, *, for_share: bool = False) -> KycStatus:
    query = select(Application.status).where(Application.user_id == user_id)
    if for_share:
        # Held until the caller commits: a submission or a decision waits for it, and it waits
        # for theirs, so the caller acts on a status that cannot change under it.
        query = query.with_for_update(read=True)
    status = await db.scalar(query)
    return KycStatus(status) if status is not None else KycStatus.NOT_SUBMITTED


@dataclass(frozen=True)
class PendingRecord:
    id: UUID
    user_id: UUID
    submitted_at: datetime


async def list_pending(db: AsyncSession, limit: int) -> list[PendingRecord]:
    rows = await db.execute(
        select(Application.id, Application.user_id, Application.submitted_at)
        .where(Application.status == KycStatus.PENDING)
        .order_by(Application.submitted_at, Application.id)
        .limit(limit)
    )
    pending = []
    for row in rows:
        if row.submitted_at is None:
            raise RuntimeError("a pending application without a submission time")
        pending.append(PendingRecord(row.id, row.user_id, row.submitted_at))
    return pending


async def count_pending(db: AsyncSession) -> int:
    count = await db.scalar(
        select(func.count()).select_from(Application).where(Application.status == KycStatus.PENDING)
    )
    return count or 0


async def ensure_application(db: AsyncSession, user_id: UUID, now: datetime) -> None:
    # ON CONFLICT: two first uploads at once must not both insert.
    await db.execute(
        insert(Application)
        .values(user_id=user_id, status=KycStatus.NOT_SUBMITTED, created_at=now, updated_at=now)
        .on_conflict_do_nothing(index_elements=[Application.user_id])
    )


@dataclass(frozen=True)
class StoredPhoto:
    kind: DocumentKind
    object_key: str
    size_bytes: int


async def save_document(
    db: AsyncSession, application_id: UUID, photo: StoredPhoto, now: datetime
) -> None:
    values = {"object_key": photo.object_key, "size_bytes": photo.size_bytes, "uploaded_at": now}
    statement = insert(Document).values(application_id=application_id, kind=photo.kind, **values)
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=[Document.application_id, Document.kind], set_=values
        )
    )
    await _touch(db, application_id, now)


async def set_submitted(db: AsyncSession, application_id: UUID, now: datetime) -> None:
    await db.execute(
        update(Application)
        .where(Application.id == application_id)
        # A resubmission waits for a new decision: the old one must not show on the card.
        .values(
            status=KycStatus.PENDING,
            rejection_reason=None,
            submitted_at=now,
            decided_at=None,
            updated_at=now,
        )
    )


async def set_decision(
    db: AsyncSession, application_id: UUID, decided: KycApplication, now: datetime
) -> None:
    await db.execute(
        update(Application)
        .where(Application.id == application_id)
        .values(
            status=decided.status,
            rejection_reason=decided.rejection_reason,
            license_issued_on=decided.license_issued_on,
            decided_at=now,
            updated_at=now,
        )
    )


async def _touch(db: AsyncSession, application_id: UUID, now: datetime) -> None:
    await db.execute(
        update(Application).where(Application.id == application_id).values(updated_at=now)
    )
