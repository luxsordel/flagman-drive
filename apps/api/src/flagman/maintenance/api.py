from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit.dependencies import get_request_origin
from flagman.audit.domain import RequestOrigin
from flagman.core.alerts import SpikeMeter
from flagman.core.db import get_session
from flagman.core.errors import RATE_LIMITED_RESPONSE, Problem
from flagman.core.photos import PhotoTooLargeError, photo_request_body, read_photo_body
from flagman.core.ratelimit import RateLimiter, get_rate_limiter
from flagman.core.resources import Resources, get_resources
from flagman.core.storage import ObjectStorage, object_storage_from_settings
from flagman.fleet import service as fleet
from flagman.fleet.ports import TelematicsGateway
from flagman.identity.dependencies import (
    CurrentStaff,
    require_live_staff_session,
    require_staff_role,
)
from flagman.identity.domain import StaffRole
from flagman.maintenance.domain import SIGNED_URL_TTL, TaskPhotoTooLargeError, Worker
from flagman.maintenance.schemas import (
    AssignIn,
    CancelIn,
    CompleteIn,
    PhotoUrlOut,
    TaskCommandIn,
    TaskCommandOut,
    TaskDetailsOut,
    TaskIn,
    TaskList,
    TaskOut,
    TaskPhotoOut,
    TaskPhotosOut,
    TasksQuery,
    TechnicianList,
    TechnicianOut,
)
from flagman.maintenance.service import (
    CommandRequest,
    MaintenanceService,
    NewTaskRequest,
    TaskWork,
    maintenance_rules,
)
from flagman.rentals.domain import PhotoStorageUnavailableError

router = APIRouter(
    prefix="/admin/maintenance",
    tags=["admin-maintenance"],
    responses={
        HTTPStatus.UNAUTHORIZED: {
            "model": Problem,
            "description": "`urn:flagman:problem:access-token-invalid`: refresh or sign in.",
        },
        HTTPStatus.FORBIDDEN: {
            "model": Problem,
            "description": "`urn:flagman:problem:forbidden`: not a role of this action.",
        },
    },
)

TASK_WORKERS = (StaffRole.OPERATOR, StaffRole.TECHNICIAN, StaffRole.ADMIN)
# Only they plan the work; technicians take it (docs/adr/0025).
TASK_PLANNERS = (StaffRole.OPERATOR, StaffRole.ADMIN)

AnyWorker = Annotated[CurrentStaff, Depends(require_staff_role(*TASK_WORKERS))]
Planner = Annotated[CurrentStaff, Depends(require_staff_role(*TASK_PLANNERS))]
# Commands to cars, and taking a car off the client map, wait for no token expiry after a
# sign-out, as the fleet's do.
LiveWorker = Annotated[CurrentStaff, Depends(require_live_staff_session(*TASK_WORKERS))]
Db = Annotated[AsyncSession, Depends(get_session)]


def _worker(staff: CurrentStaff) -> Worker:
    return Worker(staff_id=staff.staff_id, role=staff.role)


def get_service(
    db: Db,
    resources: Annotated[Resources, Depends(get_resources)],
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
) -> MaintenanceService:
    return MaintenanceService(db=db, rules=maintenance_rules(resources.settings), origin=origin)


def get_task_work(
    db: Db,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    resources: Annotated[Resources, Depends(get_resources)],
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
) -> TaskWork:
    return TaskWork(
        db=db, limiter=limiter, rules=maintenance_rules(resources.settings), origin=origin
    )


def get_photo_storage(resources: Annotated[Resources, Depends(get_resources)]) -> ObjectStorage:
    storage = object_storage_from_settings(resources.settings)
    if storage is None:
        raise PhotoStorageUnavailableError("No photo storage is configured.")
    return storage


def get_telematics_gateway(
    request: Request, resources: Annotated[Resources, Depends(get_resources)]
) -> TelematicsGateway:
    return fleet.create_telematics_gateway(resources.settings, request.app.state.telematics_client)


def get_car_commander(
    staff: LiveWorker,
    db: Db,
    origin: Annotated[RequestOrigin, Depends(get_request_origin)],
    gateway: Annotated[TelematicsGateway, Depends(get_telematics_gateway)],
    resources: Annotated[Resources, Depends(get_resources)],
) -> fleet.CarCommander:
    return fleet.CarCommander(
        db=db,
        gateway=gateway,
        alerts=SpikeMeter(resources.redis),
        staff_id=staff.staff_id,
        origin=origin,
        max_skew=fleet.telemetry_clock_skew(resources.settings),
    )


def _no_store(response: Response) -> None:
    # The answer carries a signed link to a photo; a cached copy would hand it out again later.
    response.headers["Cache-Control"] = "no-store"


Service = Annotated[MaintenanceService, Depends(get_service)]
Work = Annotated[TaskWork, Depends(get_task_work)]
Storage = Annotated[ObjectStorage, Depends(get_photo_storage)]
Commander = Annotated[fleet.CarCommander, Depends(get_car_commander)]


def _problem(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description}


_NOT_FOUND = _problem(
    "`urn:flagman:problem:maintenance-task-not-found`, also for a task a technician may not see."
)
_TRANSITION = "`urn:flagman:problem:maintenance-task-transition-not-allowed`: not from this status."
_NOT_IN_PROGRESS = (
    "`urn:flagman:problem:maintenance-task-not-in-progress`: not your task in progress."
)
_STORAGE_UNAVAILABLE = _problem("`urn:flagman:problem:photo-storage-unavailable`.")


@router.get(
    "/tasks",
    summary="Tasks: all of them for planners, free and own ones for a technician",
)
async def list_tasks(
    staff: AnyWorker, service: Service, query: Annotated[TasksQuery, Query()]
) -> TaskList:
    page = await service.list_tasks(_worker(staff), query.filter(), query.limit, query.offset)
    return TaskList.of(page)


@router.get(
    "/technicians",
    summary="Active technicians to assign tasks to",
)
async def list_technicians(staff: Planner, service: Service) -> TechnicianList:
    technicians = await service.list_technicians()
    return TechnicianList(
        items=[TechnicianOut(id=id_, email=email) for id_, email in technicians.items()]
    )


@router.post(
    "/tasks",
    status_code=HTTPStatus.CREATED,
    summary="Create a task for a car, optionally assigned to a technician",
    responses={
        HTTPStatus.NOT_FOUND: _problem("`urn:flagman:problem:car-not-found`."),
        HTTPStatus.CONFLICT: _problem(
            "`urn:flagman:problem:maintenance-task-duplicate`: the car has an open task of "
            "this type."
        ),
        HTTPStatus.UNPROCESSABLE_ENTITY: _problem(
            "`urn:flagman:problem:maintenance-assignee-invalid`: not an active technician."
        ),
    },
)
async def create_task(staff: Planner, body: TaskIn, service: Service) -> TaskOut:
    request = NewTaskRequest(
        car_id=body.car_id,
        type=body.type,
        description=body.description,
        assignee_id=body.assignee_id,
    )
    task = await service.create_task(staff.staff_id, request, datetime.now(UTC))
    return await _details(service, staff, task.id)


@router.get(
    "/tasks/{task_id}",
    summary="One task with its car and photos",
    responses={HTTPStatus.NOT_FOUND: _NOT_FOUND},
)
async def get_task(task_id: UUID, staff: AnyWorker, service: Service) -> TaskDetailsOut:
    return TaskDetailsOut.of_details(await service.get_task(_worker(staff), task_id))


async def _details(service: MaintenanceService, staff: CurrentStaff, task_id: UUID) -> TaskOut:
    return TaskOut.of((await service.get_task(_worker(staff), task_id)).view)


@router.post(
    "/tasks/{task_id}/assign",
    summary="Assign the task to a technician, or reassign it before it starts",
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(_TRANSITION),
        HTTPStatus.UNPROCESSABLE_ENTITY: _problem(
            "`urn:flagman:problem:maintenance-assignee-invalid`: not an active technician."
        ),
    },
)
async def assign_task(task_id: UUID, staff: Planner, body: AssignIn, service: Service) -> TaskOut:
    await service.assign(_worker(staff), task_id, body.assignee_id, datetime.now(UTC))
    return await _details(service, staff, task_id)


@router.post(
    "/tasks/{task_id}/start",
    summary="Take the task: the car goes to service and leaves the client map",
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(
            _TRANSITION + " `maintenance-car-busy`: the car is reserved or rented; "
            "`maintenance-task-assigned`: another technician has it."
        ),
    },
)
async def start_task(task_id: UUID, staff: LiveWorker, service: Service) -> TaskOut:
    await service.start(_worker(staff), task_id, datetime.now(UTC))
    return await _details(service, staff, task_id)


@router.post(
    "/tasks/{task_id}/complete",
    summary="Finish your task in progress",
    description=(
        "The car goes back to clients if no other task holds it and its charge is enough; "
        "otherwise it stays in service. A charging task then stops the charging; the task stays "
        "finished whatever the car answers."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(
            _NOT_IN_PROGRESS + " `maintenance-car-doors-open`; "
            "`maintenance-charge-too-low`: a charging task waits for its charge."
        ),
    },
)
async def complete_task(  # noqa: PLR0913, PLR0917 - FastAPI dependencies
    task_id: UUID,
    staff: LiveWorker,
    body: CompleteIn,
    service: Service,
    work: Work,
    commander: Commander,
) -> TaskOut:
    now = datetime.now(UTC)
    task = await service.complete(_worker(staff), task_id, body.comment, now)
    await work.stop_charging(commander, task, now)
    return await _details(service, staff, task_id)


@router.post(
    "/tasks/{task_id}/cancel",
    summary="Cancel an open task; a car in service goes back to clients if it may",
    description="A charging task already taken also stops the charging, as when it is finished.",
    responses={HTTPStatus.NOT_FOUND: _NOT_FOUND, HTTPStatus.CONFLICT: _problem(_TRANSITION)},
)
async def cancel_task(  # noqa: PLR0913, PLR0917 - FastAPI dependencies
    task_id: UUID,
    staff: Planner,
    body: CancelIn,
    service: Service,
    work: Work,
    commander: Commander,
) -> TaskOut:
    now = datetime.now(UTC)
    task = await service.cancel(_worker(staff), task_id, body.comment, now)
    await work.stop_charging(commander, task, now)
    return await _details(service, staff, task_id)


@router.post(
    "/tasks/{task_id}/commands",
    summary="Service access: open, close, plug in or out the car of your task in progress",
    description=(
        "Only within reach of the car, by its last fresh telemetry. Charging commands are for "
        "charging tasks only. Audited as `car.command_sent` with the task id."
    ),
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(
            _NOT_IN_PROGRESS + " `maintenance-command-not-for-task`; `too-far-from-car`; "
            "`car-location-unknown`: no fresh telemetry from the car; "
            "`car-command-rejected`: the car refused, `detail` is its reason."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _problem(
            "`urn:flagman:problem:telematics-unavailable`: nothing was done, try again."
        ),
        HTTPStatus.GATEWAY_TIMEOUT: _problem(
            "`urn:flagman:problem:telematics-timeout`: the car may or may not have done it."
        ),
    },
)
async def send_task_command(  # noqa: PLR0913, PLR0917 - FastAPI dependencies
    task_id: UUID,
    staff: LiveWorker,
    body: TaskCommandIn,
    work: Work,
    service: Service,
    commander: Commander,
) -> TaskCommandOut:
    request = CommandRequest(task_id=task_id, command=body.command, technician_at=body.point())
    result = await work.send_command(commander, _worker(staff), request, datetime.now(UTC))
    return TaskCommandOut(
        command_id=result.command_id, task=await _details(service, staff, task_id)
    )


async def _read_photo(request: Request) -> bytes:
    try:
        return await read_photo_body(request)
    except PhotoTooLargeError as error:
        raise TaskPhotoTooLargeError from error


@router.post(
    "/tasks/{task_id}/photos",
    summary="Add a photo to your task in progress",
    description="Photos are evidence: they cannot be deleted or replaced once uploaded.",
    openapi_extra=photo_request_body("The photo of the work done"),
    responses={
        HTTPStatus.NOT_FOUND: _NOT_FOUND,
        HTTPStatus.CONFLICT: _problem(_NOT_IN_PROGRESS + " `maintenance-task-photo-limit`."),
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE: _problem(
            "`urn:flagman:problem:maintenance-task-photo-too-large`."
        ),
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: _problem(
            "`urn:flagman:problem:maintenance-task-photo-type-unsupported`: not a photo."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
    },
)
async def add_task_photo(
    task_id: UUID, staff: AnyWorker, request: Request, work: Work, storage: Storage
) -> TaskPhotosOut:
    worker = _worker(staff)
    await work.count_upload(worker)
    photo = await _read_photo(request)
    photos = await work.add_photo(storage, worker, task_id, photo, datetime.now(UTC))
    return TaskPhotosOut(photos=[TaskPhotoOut(id=p.id, uploaded_at=p.uploaded_at) for p in photos])


@router.get(
    "/tasks/{task_id}/photos/{photo_id}/url",
    summary="A short-lived link to a photo of the task",
    dependencies=[Depends(_no_store)],
    responses={
        HTTPStatus.NOT_FOUND: _problem(
            "`urn:flagman:problem:maintenance-task-not-found`, `maintenance-task-photo-not-found`."
        ),
        HTTPStatus.TOO_MANY_REQUESTS: RATE_LIMITED_RESPONSE,
        HTTPStatus.SERVICE_UNAVAILABLE: _STORAGE_UNAVAILABLE,
    },
)
async def get_task_photo_url(
    task_id: UUID, photo_id: UUID, staff: AnyWorker, work: Work, storage: Storage
) -> PhotoUrlOut:
    url = await work.photo_url(storage, _worker(staff), task_id, photo_id)
    return PhotoUrlOut(url=url, expires_in=int(SIGNED_URL_TTL.total_seconds()))
