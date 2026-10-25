from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import Problem
from flagman.geo import service
from flagman.geo.domain import Zone
from flagman.geo.schemas import PolygonGeometry, ZoneIn, ZoneList, ZoneOut
from flagman.identity.dependencies import CurrentStaff, require_staff_role
from flagman.identity.domain import StaffRole

# Zone ids in the path are fine: every staff member may see every zone, and nobody else gets
# past the role check.
router = APIRouter(
    prefix="/admin/zones",
    tags=["zones"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: sign in as staff.",
        },
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": "`urn:flagman:problem:forbidden`: only `admin` changes zones.",
        },
    },
)

Viewer = Annotated[
    CurrentStaff,
    Depends(require_staff_role(StaffRole.OPERATOR, StaffRole.TECHNICIAN, StaffRole.ADMIN)),
]
Editor = Annotated[CurrentStaff, Depends(require_staff_role(StaffRole.ADMIN))]
Db = Annotated[AsyncSession, Depends(get_session)]
Origin = Annotated[RequestOrigin, Depends(get_request_origin)]

_NOT_FOUND: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:zone-not-found`.",
}
_POLYGON_INVALID: dict[str, Any] = {
    "model": Problem,
    "description": (
        "`urn:flagman:problem:validation-error`: see `errors`. The polygon breaks a rule: "
        "`urn:flagman:problem:zone-polygon-not-closed`, `zone-polygon-too-few-vertices`, "
        "`zone-polygon-too-many-vertices`, `zone-polygon-holes`, "
        "`zone-polygon-self-intersecting`, `zone-polygon-outside-area`."
    ),
}
_DISABLED: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:zone-disabled`: draw a new zone instead.",
}


def _out(zone: Zone) -> ZoneOut:
    return ZoneOut(
        id=zone.id,
        name=zone.name,
        kind=zone.kind,
        polygon=PolygonGeometry.of(zone.polygon),
        created_at=zone.created_at,
        updated_at=zone.updated_at,
        disabled_at=zone.disabled_at,
    )


@router.get("", summary="Zones in the order they were drawn")
async def list_zones(
    staff: Viewer,
    db: Db,
    include_disabled: Annotated[bool, Query(description="Also list disabled zones.")] = False,
) -> ZoneList:
    zones = await service.list_zones(db, include_disabled=include_disabled)
    return ZoneList(items=[_out(zone) for zone in zones])


@router.get("/{zone_id}", summary="One zone", responses={HTTPStatus.NOT_FOUND: _NOT_FOUND})
async def get_zone(staff: Viewer, zone_id: UUID, db: Db) -> ZoneOut:
    return _out(await service.get_zone(db, zone_id))


def get_zone_editor(staff: Editor, db: Db, origin: Origin) -> service.ZoneEditor:
    return service.ZoneEditor(db=db, staff_id=staff.staff_id, origin=origin)


ZoneEditor = Annotated[service.ZoneEditor, Depends(get_zone_editor)]


def _change(body: ZoneIn) -> service.ZoneChange:
    return service.ZoneChange(name=body.name, kind=body.kind, rings=body.polygon.coordinates)


@router.post(
    "",
    summary="Draw a zone",
    status_code=HTTPStatus.CREATED,
    responses={HTTPStatus.UNPROCESSABLE_ENTITY: _POLYGON_INVALID},
)
async def create_zone(body: ZoneIn, editor: ZoneEditor) -> ZoneOut:
    return _out(await editor.create(_change(body), datetime.now(UTC)))


@router.put(
    "/{zone_id}",
    summary="Replace the name, the kind and the outline of a zone",
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _DISABLED,
        HTTPStatus.UNPROCESSABLE_ENTITY: _POLYGON_INVALID,
    },
)
async def update_zone(zone_id: UUID, body: ZoneIn, editor: ZoneEditor) -> ZoneOut:
    return _out(await editor.update(zone_id, _change(body), datetime.now(UTC)))


@router.post(
    "/{zone_id}/disable",
    summary="Take a zone out of every check; it stays in the history",
    description="Disabling a disabled zone returns it as it is.",
    responses={HTTPStatus.NOT_FOUND: _NOT_FOUND},
)
async def disable_zone(zone_id: UUID, editor: ZoneEditor) -> ZoneOut:
    return _out(await editor.disable(zone_id, datetime.now(UTC)))
