"""Technician tasks: statuses, who may do what, the charging task rule. No I/O here (ADR-0025)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus

from flagman.core.errors import AppError
from flagman.fleet.domain import CarState, CarStatus
from flagman.identity.domain import StaffRole

DESCRIPTION_MAX_LENGTH = 500
COMMENT_MIN_LENGTH = 3
COMMENT_MAX_LENGTH = 500
MAX_TASK_PHOTOS = 8
PAGE_SIZE_MAX = 100
# Short-lived like inspection photos (docs/03-security.md): a link that leaks stops working soon.
SIGNED_URL_TTL = timedelta(minutes=5)


class TaskType(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    CHARGING = "charging"
    WASHING = "washing"
    INSPECTION = "inspection"
    REPAIR = "repair"
    OTHER = "other"


class TaskStatus(StrEnum):
    """Values are stored as is: renaming one needs a migration."""

    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


OPEN_STATUSES = frozenset({TaskStatus.NEW, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS})


class TaskTransition(StrEnum):
    ASSIGN = "assign"
    START = "start"
    COMPLETE = "complete"
    CANCEL = "cancel"


@dataclass(frozen=True)
class _Rule:
    sources: frozenset[TaskStatus]
    target: TaskStatus


# A task in progress is not reassigned: the car is in service in someone's hands, the operator
# cancels the task and creates a new one instead.
_RULES: dict[TaskTransition, _Rule] = {
    TaskTransition.ASSIGN: _Rule(
        frozenset({TaskStatus.NEW, TaskStatus.ASSIGNED}), TaskStatus.ASSIGNED
    ),
    TaskTransition.START: _Rule(
        frozenset({TaskStatus.NEW, TaskStatus.ASSIGNED}), TaskStatus.IN_PROGRESS
    ),
    TaskTransition.COMPLETE: _Rule(frozenset({TaskStatus.IN_PROGRESS}), TaskStatus.DONE),
    TaskTransition.CANCEL: _Rule(OPEN_STATUSES, TaskStatus.CANCELLED),
}


class MaintenanceCommand(StrEnum):
    """Service access of a technician; values are the fleet's car commands (ADR-0025)."""

    OPEN = "open"
    CLOSE = "close"
    START_CHARGING = "start_charging"
    STOP_CHARGING = "stop_charging"


class TaskNotFoundError(AppError):
    """Also for a task a technician may not see: telling it exists helps to guess ids."""

    status = HTTPStatus.NOT_FOUND
    slug = "maintenance-task-not-found"
    title = "Task not found"


class TaskTransitionNotAllowedError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "maintenance-task-transition-not-allowed"
    title = "The task cannot change to this status now"


class TaskDuplicateError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "maintenance-task-duplicate"
    title = "The car already has an open task of this type"


class TaskCarBusyError(AppError):
    """A reserved or rented car: a client holds it, the fleet does not pull it out."""

    status = HTTPStatus.CONFLICT
    slug = "maintenance-car-busy"
    title = "The car is reserved or rented; wait until the rental ends"


class ChargeTooLowError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "maintenance-charge-too-low"
    title = "The car is not charged enough to finish the charging task"


class TaskNotInProgressError(AppError):
    """Service access is for the technician's own task in progress only."""

    status = HTTPStatus.CONFLICT
    slug = "maintenance-task-not-in-progress"
    title = "Start the task before sending commands to the car"


class CommandNotForTaskError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "maintenance-command-not-for-task"
    title = "Charging commands are for charging tasks only"


class TaskPhotoLimitError(AppError):
    status = HTTPStatus.CONFLICT
    slug = "maintenance-task-photo-limit"
    title = f"A task takes at most {MAX_TASK_PHOTOS} photos"


class TaskPhotoNotFoundError(AppError):
    status = HTTPStatus.NOT_FOUND
    slug = "maintenance-task-photo-not-found"
    title = "Photo not found"


class TaskPhotoTooLargeError(AppError):
    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    slug = "maintenance-task-photo-too-large"
    title = "The photo is too large"


class TaskPhotoTypeUnsupportedError(AppError):
    status = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    slug = "maintenance-task-photo-type-unsupported"
    title = "The photo must be a JPEG, PNG or WebP image"


class TaskNotAssignableError(AppError):
    """Only an active technician takes tasks."""

    status = HTTPStatus.UNPROCESSABLE_ENTITY
    slug = "maintenance-assignee-invalid"
    title = "Tasks are assigned to active technicians only"


@dataclass(frozen=True)
class Task:
    id: uuid.UUID
    car_id: uuid.UUID
    type: TaskType
    status: TaskStatus
    description: str | None
    assignee_id: uuid.UUID | None
    # None for a task the worker created (ADR-0025).
    created_by: uuid.UUID | None
    comment: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_free(self) -> bool:
        return self.status is TaskStatus.NEW and self.assignee_id is None


@dataclass(frozen=True)
class Worker:
    """The staff member acting on a task."""

    staff_id: uuid.UUID
    role: StaffRole

    @property
    def is_technician(self) -> bool:
        return self.role is StaffRole.TECHNICIAN


def apply_transition(status: TaskStatus, transition: TaskTransition) -> TaskStatus:
    rule = _RULES[transition]
    if status not in rule.sources:
        raise TaskTransitionNotAllowedError
    return rule.target


def can_see(task: Task, worker: Worker) -> bool:
    # A technician sees the queue to take from and their own work, not the colleagues' tasks.
    if not worker.is_technician:
        return True
    return task.is_free or task.assignee_id == worker.staff_id


def ensure_can_start(task: Task, worker: Worker) -> None:
    """Whoever takes the task becomes its assignee; a technician takes free tasks or their own."""
    if worker.is_technician and not can_see(task, worker):
        raise TaskNotFoundError
    apply_transition(task.status, TaskTransition.START)


def ensure_own_task_in_progress(task: Task, worker: Worker) -> None:
    """For completing a task and for service access: only its assignee, only while in progress."""
    if worker.is_technician and task.assignee_id != worker.staff_id:
        raise TaskNotFoundError
    # Operators see every task but work only on the ones they took themselves.
    if task.status is not TaskStatus.IN_PROGRESS or task.assignee_id != worker.staff_id:
        raise TaskNotInProgressError


_CHARGING_COMMANDS = frozenset(
    {MaintenanceCommand.START_CHARGING, MaintenanceCommand.STOP_CHARGING}
)


def ensure_command_fits(task: Task, command: MaintenanceCommand) -> None:
    # Plugging a car in during a wash would only hide that it still needs charging.
    if command in _CHARGING_COMMANDS and task.type is not TaskType.CHARGING:
        raise CommandNotForTaskError


def ensure_can_complete(task: Task, state: CarState | None, min_battery_pct: int) -> None:
    if task.type is TaskType.CHARGING and (state is None or state.battery_pct < min_battery_pct):
        raise ChargeTooLowError
    apply_transition(task.status, TaskTransition.COMPLETE)


def returns_to_available(
    state: CarState | None, min_battery_pct: int, other_tasks_in_progress: bool
) -> bool:
    """Whether a car in service goes back to clients when its task ends.

    A car still below the charge clients get stays in service, visible in the fleet: going
    back to `available` would only hide it from clients behind the availability rule.
    """
    if other_tasks_in_progress or state is None:
        return False
    return state.battery_pct >= min_battery_pct


@dataclass(frozen=True)
class ChargingRules:
    threshold_pct: int
    max_state_age: timedelta


def needs_charging_task(
    status: CarStatus,
    state: CarState | None,
    has_open_task: bool,
    rules: ChargingRules,
    now: datetime,
) -> bool:
    """The worker's rule: a free car running low, with fresh telemetry and no task yet.

    A reserved or rented car is left alone until the rental ends; an old snapshot may show a
    charge the car no longer has, either way.
    """
    if status is not CarStatus.AVAILABLE or state is None or has_open_task:
        return False
    if now - state.recorded_at > rules.max_state_age:
        return False
    return state.battery_pct < rules.threshold_pct


@dataclass(frozen=True)
class TaskFilter:
    """What a list shows; no statuses means every status."""

    statuses: frozenset[TaskStatus] = frozenset()
    types: frozenset[TaskType] = frozenset()
    car_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    # A technician's "free" tab: new tasks without an assignee.
    free_only: bool = False
