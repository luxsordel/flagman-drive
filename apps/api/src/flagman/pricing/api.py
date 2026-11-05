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
    FineTypeCreate,
    FineTypeList,
    FineTypeUpdate,
    FineTypeVersionOut,
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


fine_types_router = APIRouter(
    prefix="/admin/fine-types",
    tags=["fines"],
    responses={
        HTTPStatus.UNAUTHORIZED: router.responses[HTTPStatus.UNAUTHORIZED],
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:forbidden`: `operator` and `admin` see the fine catalog, "
                "only `admin` changes it."
            ),
        },
    },
)

_FINE_TYPE_NOT_FOUND: dict[str, Any] = {
    "model": Problem,
    "description": "`urn:flagman:problem:fine-type-not-found`.",
}


@fine_types_router.get("", summary="The fine catalog: every type with its current version")
async def list_fine_types(staff: Viewer, db: Db) -> FineTypeList:
    items = await service.list_fine_types(db)
    return FineTypeList(items=[FineTypeVersionOut.of(item) for item in items])


@fine_types_router.get(
    "/{fine_type_id}/versions",
    summary="Every version of a fine type, newest first",
    responses={HTTPStatus.NOT_FOUND: _FINE_TYPE_NOT_FOUND},
)
async def list_fine_type_versions(staff: Viewer, fine_type_id: UUID, db: Db) -> FineTypeList:
    versions = await service.list_fine_type_versions(db, fine_type_id)
    return FineTypeList(items=[FineTypeVersionOut.of(version) for version in versions])


def get_fine_catalog_editor(staff: Editor, db: Db, origin: Origin) -> service.FineCatalogEditor:
    return service.FineCatalogEditor(db=db, staff_id=staff.staff_id, origin=origin)


FineCatalogEditor = Annotated[service.FineCatalogEditor, Depends(get_fine_catalog_editor)]


@fine_types_router.post(
    "",
    summary="Add a manual fine type",
    status_code=HTTPStatus.CREATED,
    responses={
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": "`urn:flagman:problem:fine-type-exists`: the code is taken.",
        },
    },
)
async def create_fine_type(body: FineTypeCreate, editor: FineCatalogEditor) -> FineTypeVersionOut:
    version = await editor.create(body.code, body.terms(), datetime.now(UTC))
    return FineTypeVersionOut.of(version)


@fine_types_router.put(
    "/{fine_type_id}",
    summary="Save new names or amount of a fine type",
    description="Adds a version that applies from now on. Fines charged before keep theirs.",
    responses={
        HTTPStatus.NOT_FOUND: _FINE_TYPE_NOT_FOUND,
        HTTPStatus.CONFLICT: {
            "model": Problem,
            "description": (
                "`urn:flagman:problem:fine-type-changed`: a newer version than "
                "`based_on_version` was saved meanwhile; reload the catalog."
            ),
        },
    },
)
async def update_fine_type(
    fine_type_id: UUID, body: FineTypeUpdate, editor: FineCatalogEditor
) -> FineTypeVersionOut:
    version = await editor.update(
        fine_type_id, body.based_on_version, body.terms(), datetime.now(UTC)
    )
    return FineTypeVersionOut.of(version)
