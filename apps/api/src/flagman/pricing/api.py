from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.db import get_session
from flagman.core.errors import Problem
from flagman.identity.dependencies import CurrentStaff, require_staff_role
from flagman.identity.domain import StaffRole
from flagman.pricing import service
from flagman.pricing.schemas import (
    ModelTariffList,
    ModelTariffOut,
    TariffCreate,
    TariffUpdate,
    TariffVersionList,
    TariffVersionOut,
)

# Tariff ids in the path are fine: every operator and admin may see every tariff, and nobody
# else gets past the role check.
router = APIRouter(
    prefix="/admin/tariffs",
    tags=["tariffs"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: sign in as staff.",
        },
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:forbidden`: `operator` and `admin` see tariffs, "
                "only `admin` changes them."
            ),
        },
    },
)

Viewer = Annotated[CurrentStaff, Depends(require_staff_role(StaffRole.OPERATOR, StaffRole.ADMIN))]
Editor = Annotated[CurrentStaff, Depends(require_staff_role(StaffRole.ADMIN))]
Db = Annotated[AsyncSession, Depends(get_session)]
Origin = Annotated[RequestOrigin, Depends(get_request_origin)]

_NOT_FOUND: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:tariff-not-found`.",
}


@router.get("", summary="Every car model with its current tariff, by brand and name")
async def list_tariffs(staff: Viewer, db: Db) -> ModelTariffList:
    items = await service.list_tariffs(db)
    return ModelTariffList(items=[ModelTariffOut.of(item) for item in items])


@router.get("/{tariff_id}", summary="One tariff", responses={HTTPStatus.NOT_FOUND: _NOT_FOUND})
async def get_tariff(staff: Viewer, tariff_id: UUID, db: Db) -> ModelTariffOut:
    return ModelTariffOut.of(await service.get_tariff(db, tariff_id))


@router.get(
    "/{tariff_id}/versions",
    summary="Every version of a tariff, newest first",
    responses={HTTPStatus.NOT_FOUND: _NOT_FOUND},
)
async def list_tariff_versions(staff: Viewer, tariff_id: UUID, db: Db) -> TariffVersionList:
    versions = await service.list_versions(db, tariff_id)
    return TariffVersionList(items=[TariffVersionOut.of(version) for version in versions])


def get_tariff_editor(staff: Editor, db: Db, origin: Origin) -> service.TariffEditor:
    return service.TariffEditor(db=db, staff_id=staff.staff_id, origin=origin)


TariffEditor = Annotated[service.TariffEditor, Depends(get_tariff_editor)]


@router.post(
    "",
    summary="Create the tariff of a car model",
    status_code=HTTPStatus.CREATED,
    responses={
        HTTPStatus.NOT_FOUND: {
            "model": Problem,
            "description": "`urn:flagman:problem:car-model-not-found`.",
        },
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": "`urn:flagman:problem:tariff-exists`: change that tariff instead.",
        },
    },
)
async def create_tariff(body: TariffCreate, editor: TariffEditor) -> ModelTariffOut:
    return ModelTariffOut.of(await editor.create(body.model_id, body.prices(), datetime.now(UTC)))


@router.put(
    "/{tariff_id}",
    summary="Save new prices of a tariff",
    description=(
        "Adds a version that applies from now on. Rentals booked before keep the version they "
        "were booked with."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:tariff-changed`: a newer version than `based_on_version` "
                "was saved meanwhile; reload the tariff."
            ),
        },
    },
)
async def update_tariff(
    tariff_id: UUID, body: TariffUpdate, editor: TariffEditor
) -> ModelTariffOut:
    tariff = await editor.update(tariff_id, body.based_on_version, body.prices(), datetime.now(UTC))
    return ModelTariffOut.of(tariff)
