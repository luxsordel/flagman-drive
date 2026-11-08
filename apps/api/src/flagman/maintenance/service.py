"""Technician tasks and their service access to cars (docs/02-architecture.md, docs/adr/0025).

A task and its car change in one transaction, always locking the task's row and then the car's:
two tasks of one car and a reservation of it wait for each other, so each decides by what the
other has committed. A command to the car is sent outside any transaction, as the fleet does.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from flagman.audit import service as audit
from flagman.audit.domain import (
    SYSTEM,
    Actor,
    ActorType,
    AuditAction,
    AuditEntry,
    AuditTarget,
    AuditTargetType,
    DetailValue,
    RequestOrigin,
)
from flagman.core.config import Settings, WorkerSettings
from flagman.core.errors import AppError
from flagman.core.geo import GeoPoint
from flagman.core.photos import (
    OUTPUT_CONTENT_TYPE,
    PhotoTooLargeError,
    PhotoTypeUnsupportedError,
    clean_photo,
)
from flagman.core.ratelimit import Limit, RateLimiter
from flagman.core.storage import ObjectStorage
from flagman.fleet import service as fleet
from flagman.fleet.domain import Car, CarCommand, CarStatus, CarTransition
from flagman.geo import service as geo
from flagman.identity import service as identity
from flagman.maintenance import repository
from flagman.maintenance.domain import (
    MAX_TASK_PHOTOS,
    SIGNED_URL_TTL,
    ChargingRules,
    MaintenanceCommand,
    Task,
    TaskCarBusyError,
    TaskDuplicateError,
    TaskFilter,
    TaskNotAssignableError,
    TaskNotFoundError,
    TaskPhotoLimitError,
    TaskPhotoNotFoundError,
    TaskPhotoTooLargeError,
    TaskPhotoTypeUnsupportedError,
    TaskTransition,
    TaskType,
    Worker,
    apply_transition,
    can_see,
    ensure_can_complete,
    ensure_can_start,
    ensure_command_fits,
    ensure_own_task_in_progress,
    needs_charging_task,
    returns_to_available,
)
from flagman.maintenance.repository import NewTask, OpenTaskTakenError, StoredPhoto
from flagman.rentals.domain import CommandRules, ensure_near_car, normalize_damage_comment
from flagman.support import service as support

logger = structlog.get_logger(__name__)

_HOUR = 3600
# Eight photos per task and a few retries on a bad network, for a shift of tasks.
UPLOAD_LIMIT = Limit("maintenance-photo-upload-staff", 120, _HOUR)
PHOTO_URL_LIMIT = Limit("maintenance-photo-url-staff", 300, _HOUR)
# Open and close around the car, plug in and out: seconds apart, not dozens a minute.
COMMAND_LIMIT = Limit("maintenance-commands-staff", 20, 60)

# The audit comment of a service access command: the task id goes in the details next to it.
_COMMAND_COMMENT = "Service access by a maintenance task"
_BUSY_STATUSES = frozenset({CarStatus.RESERVED, CarStatus.IN_RENTAL})
_CAR_COMMANDS = {
    MaintenanceCommand.OPEN: CarCommand.SERVICE_OPEN,
    MaintenanceCommand.CLOSE: CarCommand.CLOSE,
    MaintenanceCommand.START_CHARGING: CarCommand.START_CHARGING,
    MaintenanceCommand.STOP_CHARGING: CarCommand.STOP_CHARGING,
}


@dataclass(frozen=True)
class MaintenanceRules:
    min_battery_pct: int
    # A charging task ends here: below it the worker would create the next one at once.
    charged_pct: int
    commands: CommandRules


def maintenance_rules(settings: Settings) -> MaintenanceRules:
    availability = fleet.availability_rules(settings)
    return MaintenanceRules(
        min_battery_pct=availability.min_battery_pct,
        charged_pct=settings.maintenance_charging_threshold_pct or availability.min_battery_pct,
        # The rule and the distance of clients (#110): a technician stands by the car as well.
        commands=CommandRules(
            max_distance_m=settings.rental_command_max_distance_m,
            max_state_age=availability.max_state_age,
        ),
    )


def charging_rules(settings: WorkerSettings) -> ChargingRules:
    return ChargingRules(
        threshold_pct=settings.maintenance_charging_threshold_pct or settings.fleet_min_battery_pct,
        max_state_age=timedelta(seconds=settings.fleet_telemetry_max_age_seconds),
    )


@dataclass(frozen=True)
class TaskView:
    task: Task
    car: Car


@dataclass(frozen=True)
class TaskDetails:
    view: TaskView
    photos: list[StoredPhoto]


@dataclass(frozen=True)
class TaskPage:
    items: list[TaskView]
    total: int


@dataclass(frozen=True)
class NewTaskRequest:
    car_id: UUID
    type: TaskType
    description: str | None
    assignee_id: UUID | None


@dataclass(frozen=True)
class CommandRequest:
    task_id: UUID
    command: MaintenanceCommand
    # Used for the distance only: never stored or logged.
    technician_at: GeoPoint


def _normalize(text: str | None) -> str | None:
    """Squeezed like damage comments; an empty text is no text."""
    if text is None:
        return None
    return normalize_damage_comment(text) or None


@dataclass(frozen=True)
class MaintenanceService:
    db: AsyncSession
    rules: MaintenanceRules
    origin: RequestOrigin

    def _entry(
        self,
        actor: Actor,
        action: AuditAction,
        task: Task,
        details: dict[str, DetailValue] | None = None,
    ) -> AuditEntry:
        return AuditEntry(
            actor,
            action,
            self.origin,
            AuditTarget(AuditTargetType.MAINTENANCE_TASK, task.id),
            details={"car_id": task.car_id, "type": task.type.value, **(details or {})},
        )

    async def _views(self, tasks: list[Task]) -> list[TaskView]:
        cars = {car.id: car for car in await fleet.get_cars(self.db, {t.car_id for t in tasks})}
        return [TaskView(task, cars[task.car_id]) for task in tasks]

    async def _lock(self, task_id: UUID, worker: Worker) -> Task:
        task = await repository.get_task(self.db, task_id, for_update=True)
        if task is None or not can_see(task, worker):
            raise TaskNotFoundError
        return task

    async def _ensure_technician(self, staff_id: UUID) -> None:
        if staff_id not in await identity.get_active_technicians(self.db):
            raise TaskNotAssignableError

    async def list_tasks(
        self, worker: Worker, task_filter: TaskFilter, limit: int, offset: int
    ) -> TaskPage:
        async with self.db.begin():
            tasks, total = await repository.list_tasks(self.db, worker, task_filter, limit, offset)
            return TaskPage(await self._views(tasks), total)

    async def get_task(self, worker: Worker, task_id: UUID) -> TaskDetails:
        async with self.db.begin():
            task = await repository.get_task(self.db, task_id)
            if task is None or not can_see(task, worker):
                raise TaskNotFoundError
            photos = await repository.list_photos(self.db, task.id)
            return TaskDetails((await self._views([task]))[0], photos)

    async def list_technicians(self) -> dict[UUID, str]:
        async with self.db.begin():
            return await identity.get_active_technicians(self.db)

    async def create_task(self, staff_id: UUID, request: NewTaskRequest, now: datetime) -> Task:
        """By an operator or admin; raises CarNotFoundError, TaskNotAssignableError,
        TaskDuplicateError."""
        async with self.db.begin():
            await fleet.get_car(self.db, request.car_id)
            if request.assignee_id is not None:
                await self._ensure_technician(request.assignee_id)
            new = NewTask(
                car_id=request.car_id,
                type=request.type,
                description=_normalize(request.description),
                assignee_id=request.assignee_id,
                created_by=staff_id,
            )
            try:
                task = await repository.add_task(self.db, new, now)
            except OpenTaskTakenError as error:
                raise TaskDuplicateError from error
            await audit.record(
                self.db,
                self._entry(
                    Actor(ActorType.STAFF, staff_id),
                    AuditAction.MAINTENANCE_TASK_CREATED,
                    task,
                    {"assignee_id": task.assignee_id},
                ),
            )
        logger.info("maintenance_task_created", task_id=str(task.id), type=task.type)
        return task

    async def assign(self, worker: Worker, task_id: UUID, assignee_id: UUID, now: datetime) -> Task:
        async with self.db.begin():
            task = await self._lock(task_id, worker)
            status = apply_transition(task.status, TaskTransition.ASSIGN)
            await self._ensure_technician(assignee_id)
            await repository.assign(self.db, task.id, status, assignee_id, now)
            await audit.record(
                self.db,
                self._entry(
                    Actor(ActorType.STAFF, worker.staff_id),
                    AuditAction.MAINTENANCE_TASK_ASSIGNED,
                    task,
                    {"assignee_id": assignee_id, "previous_assignee_id": task.assignee_id},
                ),
            )
            return await self._get(task.id)

    async def start(self, worker: Worker, task_id: UUID, now: datetime) -> Task:
        """Takes the task: the worker becomes its assignee and the car goes to service."""
        async with self.db.begin():
            task = await self._lock(task_id, worker)
            ensure_can_start(task, worker)
            status = apply_transition(task.status, TaskTransition.START)
            car = await fleet.lock_car(self.db, task.car_id)
            if car.status in _BUSY_STATUSES:
                raise TaskCarBusyError
            if car.status is not CarStatus.SERVICE:
                await fleet.change_status(self.db, car.id, CarTransition.SEND_TO_SERVICE, now)
            await repository.start(self.db, task.id, status, worker.staff_id, now)
            await audit.record(
                self.db,
                self._entry(
                    Actor(ActorType.STAFF, worker.staff_id),
                    AuditAction.MAINTENANCE_TASK_STARTED,
                    task,
                    {"car_status": car.status.value},
                ),
            )
            return await self._get(task.id)

    async def complete(self, worker: Worker, task_id: UUID, comment: str, now: datetime) -> Task:
        async with self.db.begin():
            task = await self._lock(task_id, worker)
            ensure_own_task_in_progress(task, worker)
            car = await fleet.lock_car(self.db, task.car_id)
            ensure_can_complete(task, car.state, self.rules.charged_pct)
            status = apply_transition(task.status, TaskTransition.COMPLETE)
            await repository.finish(self.db, task.id, status, _normalize(comment), now)
            repaired = task.type is TaskType.REPAIR
            returned = await self._release_car(task, car, now, repaired=repaired)
            await audit.record(
                self.db,
                self._entry(
                    Actor(ActorType.STAFF, worker.staff_id),
                    AuditAction.MAINTENANCE_TASK_COMPLETED,
                    task,
                    {"car_returned": returned},
                ),
            )
            return await self._get(task.id)

    async def cancel(
        self, worker: Worker, task_id: UUID, comment: str | None, now: datetime
    ) -> Task:
        """By an operator or admin, from any open status."""
        async with self.db.begin():
            task = await self._lock(task_id, worker)
            status = apply_transition(task.status, TaskTransition.CANCEL)
            await repository.finish(self.db, task.id, status, _normalize(comment), now)
            returned = False
            if task.started_at is not None:
                car = await fleet.lock_car(self.db, task.car_id)
                returned = await self._release_car(task, car, now, repaired=False)
            await audit.record(
                self.db,
                self._entry(
                    Actor(ActorType.STAFF, worker.staff_id),
                    AuditAction.MAINTENANCE_TASK_CANCELLED,
                    task,
                    {"previous_status": task.status.value, "car_returned": returned},
                ),
            )
            return await self._get(task.id)

    async def _get(self, task_id: UUID) -> Task:
        task = await repository.get_task(self.db, task_id)
        if task is None:
            raise TaskNotFoundError
        return task

    async def _release_car(self, task: Task, car: Car, now: datetime, *, repaired: bool) -> bool:
        """Back to clients if nothing else holds the car in service and it is charged enough."""
        if car.status is not CarStatus.SERVICE:
            return False
        others = await repository.has_other_task_in_progress(self.db, car.id, task.id)
        held = await support.car_held_by_incident(self.db, car.id)
        if not returns_to_available(
            car.state,
            self.rules.min_battery_pct,
            others,
            held_by_incident=held,
            repaired=repaired,
        ):
            return False
        await fleet.change_status(self.db, car.id, CarTransition.RETURN_FROM_SERVICE, now)
        return True


@dataclass(frozen=True)
class TaskWork:
    """What a technician does with their task in progress: photos and service access."""

    db: AsyncSession
    limiter: RateLimiter
    rules: MaintenanceRules
    origin: RequestOrigin

    async def _own_task(self, worker: Worker, task_id: UUID) -> Task:
        task = await repository.get_task(self.db, task_id)
        if task is None or not can_see(task, worker):
            raise TaskNotFoundError
        ensure_own_task_in_progress(task, worker)
        return task

    async def count_upload(self, worker: Worker) -> None:
        """Counted before the body is read, so an exhausted limit does not cost 10 MB each time."""
        await self.limiter.hit(UPLOAD_LIMIT, str(worker.staff_id))

    async def add_photo(
        self, storage: ObjectStorage, worker: Worker, task_id: UUID, data: bytes, now: datetime
    ) -> list[StoredPhoto]:
        async with self.db.begin():
            task = await self._own_task(worker, task_id)
            if await repository.count_photos(self.db, task.id) >= MAX_TASK_PHOTOS:
                raise TaskPhotoLimitError
        try:
            photo = await asyncio.to_thread(clean_photo, data)
        except PhotoTooLargeError as error:
            raise TaskPhotoTooLargeError(str(error) or None) from error
        except PhotoTypeUnsupportedError as error:
            raise TaskPhotoTypeUnsupportedError from error
        key = f"maintenance/{task.id}/{uuid4()}.jpg"
        await storage.put(key, photo, OUTPUT_CONTENT_TYPE)
        try:
            async with self.db.begin():
                # Locked now: two parallel uploads must not both take the last place.
                locked = await repository.get_task(self.db, task.id, for_update=True)
                if locked is None:
                    raise TaskNotFoundError
                ensure_own_task_in_progress(locked, worker)
                if await repository.count_photos(self.db, task.id) >= MAX_TASK_PHOTOS:
                    raise TaskPhotoLimitError
                photo_id = await repository.add_photo(self.db, task.id, key, len(photo), now)
                await audit.record(
                    self.db,
                    AuditEntry(
                        Actor(ActorType.STAFF, worker.staff_id),
                        AuditAction.MAINTENANCE_TASK_PHOTO_ADDED,
                        self.origin,
                        AuditTarget(AuditTargetType.MAINTENANCE_TASK, task.id),
                        details={"photo_id": photo_id},
                    ),
                )
                photos = await repository.list_photos(self.db, task.id)
        except Exception:
            await _delete_quietly(storage, key)
            raise
        logger.info("maintenance_task_photo_added", task_id=str(task.id))
        return photos

    async def photo_url(
        self, storage: ObjectStorage, worker: Worker, task_id: UUID, photo_id: UUID
    ) -> str:
        await self.limiter.hit(PHOTO_URL_LIMIT, str(worker.staff_id))
        async with self.db.begin():
            task = await repository.get_task(self.db, task_id)
            if task is None or not can_see(task, worker):
                raise TaskNotFoundError
            photos = await repository.list_photos(self.db, task.id)
            key = next((photo.object_key for photo in photos if photo.id == photo_id), None)
            if key is None:
                raise TaskPhotoNotFoundError
            await audit.record(
                self.db,
                AuditEntry(
                    Actor(ActorType.STAFF, worker.staff_id),
                    AuditAction.MAINTENANCE_TASK_PHOTO_VIEWED,
                    self.origin,
                    AuditTarget(AuditTargetType.MAINTENANCE_TASK, task.id),
                    details={"photo_id": photo_id},
                ),
            )
        return await storage.signed_get_url(key, SIGNED_URL_TTL)

    async def send_command(
        self, commander: fleet.CarCommander, worker: Worker, request: CommandRequest, now: datetime
    ) -> fleet.CarCommandResult:
        """Only the assignee of the task in progress, only by the car (docs/adr/0025)."""
        await self.limiter.hit(COMMAND_LIMIT, str(worker.staff_id))
        async with self.db.begin():
            task = await self._own_task(worker, request.task_id)
            ensure_command_fits(task, request.command)
            car = await fleet.get_car(self.db, task.car_id)
            state = car.state
            distance = (
                await geo.distance_m(self.db, request.technician_at, state.location)
                if state
                else None
            )
            ensure_near_car(
                state.recorded_at if state else None, distance, self.rules.commands, now
            )
        car_request = fleet.CarCommandRequest(
            car_id=car.id,
            command=_CAR_COMMANDS[request.command],
            comment=_COMMAND_COMMENT,
            confirm_active_rental=False,
            maintenance_task_id=task.id,
        )
        return await commander.send(car_request, now)

    async def stop_charging(self, commander: fleet.CarCommander, task: Task, now: datetime) -> None:
        """After a charging task ended, so a refused finish leaves the car charging.

        No distance check: an operator cancels from the desk. The task stays closed whatever the
        car answers: the fleet audits the outcome and shows it to staff.
        """
        if task.type is not TaskType.CHARGING or task.started_at is None:
            return
        request = fleet.CarCommandRequest(
            car_id=task.car_id,
            command=CarCommand.STOP_CHARGING,
            comment=_COMMAND_COMMENT,
            confirm_active_rental=False,
            maintenance_task_id=task.id,
        )
        try:
            await commander.send(request, now)
        except AppError as error:
            logger.warning(
                "maintenance_stop_charging_failed", task_id=str(task.id), slug=error.slug
            )


async def create_charging_tasks(db: AsyncSession, rules: ChargingRules, now: datetime) -> int:
    """The worker's job: a charging task for every free car running low (docs/adr/0025).

    The cars are read in one transaction and each task is added with the car's open tasks
    checked by the unique index: a parallel run or an operator creating one meanwhile wins.
    """
    created = 0
    async with db.begin():
        cars = await fleet.list_cars(db)
        busy = await repository.car_ids_with_open_tasks(db, [car.id for car in cars])
        for car in cars:
            if not needs_charging_task(car.status, car.state, car.id in busy, rules, now):
                continue
            task = await repository.add_charging_task_if_absent(db, car.id, now)
            if task is None:
                continue
            await audit.record(
                db,
                AuditEntry(
                    SYSTEM,
                    AuditAction.MAINTENANCE_TASK_CREATED,
                    RequestOrigin(),
                    AuditTarget(AuditTargetType.MAINTENANCE_TASK, task.id),
                    details={
                        "car_id": car.id,
                        "type": task.type.value,
                        "battery_pct": car.state.battery_pct if car.state else None,
                    },
                ),
            )
            created += 1
    if created:
        logger.info("maintenance_charging_tasks_created", count=created)
    return created


async def _delete_quietly(storage: ObjectStorage, key: str) -> None:
    try:
        await storage.delete(key)
    except Exception:
        # An orphan photo is a storage cost, not a reason to fail the request.
        logger.warning("maintenance_task_photo_orphaned")
