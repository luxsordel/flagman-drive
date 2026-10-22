from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service
from flagman.audit.domain import AuditEventFilter
from flagman.audit.schemas import AuditEventPage, AuditEventQuery
from flagman.core.db import get_session
from flagman.core.errors import Problem
from flagman.identity.dependencies import require_staff_role
from flagman.identity.domain import StaffRole

router = APIRouter(prefix="/admin/audit-events", tags=["audit"])


@router.get(
    "",
    summary="List audit events, newest first",
    dependencies=[Depends(require_staff_role(StaffRole.ADMIN))],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: sign in as staff.",
        },
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": "`urn:flagman:problem:forbidden`: only `admin` reads the audit log.",
        },
    },
)
async def list_audit_events(
    query: Annotated[AuditEventQuery, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AuditEventPage:
    event_filter = AuditEventFilter(
        actor_id=query.actor_id,
        action=query.action,
        occurred_from=query.occurred_from,
        occurred_to=query.occurred_to,
    )
    return await service.list_events(db, event_filter, query.cursor, query.limit)
