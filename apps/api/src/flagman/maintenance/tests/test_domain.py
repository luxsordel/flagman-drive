import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import CarState, CarStatus
from flagman.identity.domain import StaffRole
from flagman.maintenance.domain import (
    OPEN_STATUSES,
    CarDoorsOpenError,
    ChargeTooLowError,
    ChargingRules,
    CommandNotForTaskError,
    MaintenanceCommand,
    Task,
    TaskAssignedToOtherError,
    TaskNotFoundError,
    TaskNotInProgressError,
    TaskStatus,
    TaskTransition,
    TaskTransitionNotAllowedError,
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

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
TECHNICIAN = Worker(staff_id=uuid.uuid4(), role=StaffRole.TECHNICIAN)
COLLEAGUE = Worker(staff_id=uuid.uuid4(), role=StaffRole.TECHNICIAN)
OPERATOR = Worker(staff_id=uuid.uuid4(), role=StaffRole.OPERATOR)
RULES = ChargingRules(threshold_pct=20, max_state_age=timedelta(minutes=5))


def task(**changes: object) -> Task:
    base = Task(
        id=uuid.uuid4(),
        car_id=uuid.uuid4(),
        type=TaskType.WASHING,
        status=TaskStatus.NEW,
        description=None,
        assignee_id=None,
        created_by=OPERATOR.staff_id,
        comment=None,
        created_at=NOW,
        started_at=None,
        finished_at=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def state(battery_pct: int, age: timedelta = timedelta(seconds=10)) -> CarState:
    return CarState(
        location=GeoPoint(lat=53.9, lon=27.56),
        battery_pct=battery_pct,
        doors_locked=True,
        ignition_on=False,
        engine_locked=False,
        recorded_at=NOW - age,
    )


@pytest.mark.parametrize(
    ("source", "transition", "target"),
    [
        (TaskStatus.NEW, TaskTransition.ASSIGN, TaskStatus.ASSIGNED),
        (TaskStatus.ASSIGNED, TaskTransition.ASSIGN, TaskStatus.ASSIGNED),
        (TaskStatus.NEW, TaskTransition.START, TaskStatus.IN_PROGRESS),
        (TaskStatus.ASSIGNED, TaskTransition.START, TaskStatus.IN_PROGRESS),
        (TaskStatus.IN_PROGRESS, TaskTransition.COMPLETE, TaskStatus.DONE),
        (TaskStatus.NEW, TaskTransition.CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.ASSIGNED, TaskTransition.CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.IN_PROGRESS, TaskTransition.CANCEL, TaskStatus.CANCELLED),
    ],
)
def test_allowed_transitions(
    source: TaskStatus, transition: TaskTransition, target: TaskStatus
) -> None:
    assert apply_transition(source, transition) is target


@pytest.mark.parametrize(
    ("source", "transition"),
    [
        (TaskStatus.IN_PROGRESS, TaskTransition.ASSIGN),
        (TaskStatus.IN_PROGRESS, TaskTransition.START),
        (TaskStatus.NEW, TaskTransition.COMPLETE),
        (TaskStatus.ASSIGNED, TaskTransition.COMPLETE),
        *[
            (closed, t)
            for closed in (TaskStatus.DONE, TaskStatus.CANCELLED)
            for t in TaskTransition
        ],
    ],
)
def test_refused_transitions(source: TaskStatus, transition: TaskTransition) -> None:
    with pytest.raises(TaskTransitionNotAllowedError):
        apply_transition(source, transition)


def test_open_statuses() -> None:
    assert {TaskStatus.NEW, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS} == OPEN_STATUSES


def test_technician_sees_free_and_own_tasks_only() -> None:
    assert can_see(task(), TECHNICIAN)
    assert can_see(task(status=TaskStatus.ASSIGNED, assignee_id=TECHNICIAN.staff_id), TECHNICIAN)
    assert not can_see(task(status=TaskStatus.ASSIGNED, assignee_id=COLLEAGUE.staff_id), TECHNICIAN)
    assert not can_see(task(status=TaskStatus.DONE, assignee_id=COLLEAGUE.staff_id), TECHNICIAN)


def test_operator_sees_every_task() -> None:
    assert can_see(task(status=TaskStatus.ASSIGNED, assignee_id=COLLEAGUE.staff_id), OPERATOR)


def test_cancelled_unassigned_task_is_not_free() -> None:
    assert not can_see(task(status=TaskStatus.CANCELLED), TECHNICIAN)


def test_technician_starts_free_or_own_task() -> None:
    ensure_can_start(task(), TECHNICIAN)
    ensure_can_start(task(status=TaskStatus.ASSIGNED, assignee_id=TECHNICIAN.staff_id), TECHNICIAN)


def test_technician_does_not_start_colleagues_task() -> None:
    with pytest.raises(TaskNotFoundError):
        ensure_can_start(
            task(status=TaskStatus.ASSIGNED, assignee_id=COLLEAGUE.staff_id), TECHNICIAN
        )


def test_operator_takes_a_free_task_but_not_a_technicians() -> None:
    ensure_can_start(task(), OPERATOR)
    with pytest.raises(TaskAssignedToOtherError):
        ensure_can_start(task(status=TaskStatus.ASSIGNED, assignee_id=COLLEAGUE.staff_id), OPERATOR)


def test_own_task_in_progress_passes() -> None:
    ensure_own_task_in_progress(
        task(status=TaskStatus.IN_PROGRESS, assignee_id=TECHNICIAN.staff_id), TECHNICIAN
    )


def test_colleagues_task_in_progress_is_not_found_for_technician() -> None:
    with pytest.raises(TaskNotFoundError):
        ensure_own_task_in_progress(
            task(status=TaskStatus.IN_PROGRESS, assignee_id=COLLEAGUE.staff_id), TECHNICIAN
        )


def test_own_task_not_started_refuses_work() -> None:
    with pytest.raises(TaskNotInProgressError):
        ensure_own_task_in_progress(
            task(status=TaskStatus.ASSIGNED, assignee_id=TECHNICIAN.staff_id), TECHNICIAN
        )


def test_operator_works_only_on_tasks_taken_themselves() -> None:
    with pytest.raises(TaskNotInProgressError):
        ensure_own_task_in_progress(
            task(status=TaskStatus.IN_PROGRESS, assignee_id=TECHNICIAN.staff_id), OPERATOR
        )


def test_charging_task_completes_at_threshold() -> None:
    ensure_can_complete(task(type=TaskType.CHARGING, status=TaskStatus.IN_PROGRESS), state(20), 20)


@pytest.mark.parametrize("car_state", [state(19), None])
def test_charging_task_does_not_complete_below_threshold(car_state: CarState | None) -> None:
    with pytest.raises(ChargeTooLowError):
        ensure_can_complete(
            task(type=TaskType.CHARGING, status=TaskStatus.IN_PROGRESS), car_state, 20
        )


def test_other_task_completes_whatever_the_charge() -> None:
    ensure_can_complete(task(type=TaskType.REPAIR, status=TaskStatus.IN_PROGRESS), state(5), 20)


def test_task_not_in_progress_does_not_complete() -> None:
    with pytest.raises(TaskTransitionNotAllowedError):
        ensure_can_complete(task(status=TaskStatus.ASSIGNED), state(90), 20)


def test_car_returns_to_available_when_charged_and_free() -> None:
    assert returns_to_available(state(20), 20, other_tasks_in_progress=False)


@pytest.mark.parametrize(
    ("car_state", "other_tasks"), [(state(19), False), (None, False), (state(90), True)]
)
def test_car_stays_in_service(car_state: CarState | None, other_tasks: bool) -> None:
    assert not returns_to_available(car_state, 20, other_tasks_in_progress=other_tasks)


def test_charging_task_needed_for_free_low_car() -> None:
    assert needs_charging_task(CarStatus.AVAILABLE, state(19), False, RULES, NOW)


@pytest.mark.parametrize(
    ("status", "car_state", "has_open_task"),
    [
        (CarStatus.AVAILABLE, state(20), False),
        (CarStatus.AVAILABLE, state(19), True),
        (CarStatus.AVAILABLE, state(19, age=timedelta(minutes=6)), False),
        (CarStatus.AVAILABLE, None, False),
        (CarStatus.RESERVED, state(5), False),
        (CarStatus.IN_RENTAL, state(5), False),
        (CarStatus.OFFLINE, state(5), False),
    ],
)
def test_charging_task_not_needed(
    status: CarStatus, car_state: CarState | None, has_open_task: bool
) -> None:
    assert not needs_charging_task(status, car_state, has_open_task, RULES, NOW)


def test_state_exactly_at_max_age_is_fresh() -> None:
    assert needs_charging_task(
        CarStatus.AVAILABLE, state(10, age=timedelta(minutes=5)), False, RULES, NOW
    )


@pytest.mark.parametrize("command", [MaintenanceCommand.OPEN, MaintenanceCommand.CLOSE])
def test_doors_open_for_any_task(command: MaintenanceCommand) -> None:
    ensure_command_fits(task(type=TaskType.WASHING), command)


@pytest.mark.parametrize(
    "command", [MaintenanceCommand.START_CHARGING, MaintenanceCommand.STOP_CHARGING]
)
def test_charging_commands_for_charging_task_only(command: MaintenanceCommand) -> None:
    ensure_command_fits(task(type=TaskType.CHARGING), command)
    with pytest.raises(CommandNotForTaskError):
        ensure_command_fits(task(type=TaskType.REPAIR), command)


def test_charging_task_needed_for_low_car_left_in_service() -> None:
    assert needs_charging_task(CarStatus.SERVICE, state(19), False, RULES, NOW)


def test_task_does_not_complete_with_the_doors_open() -> None:
    opened = replace(state(90), doors_locked=False)
    with pytest.raises(CarDoorsOpenError):
        ensure_can_complete(task(status=TaskStatus.IN_PROGRESS), opened, 20)


def test_unlocked_car_stays_in_service() -> None:
    opened = replace(state(90), doors_locked=False)
    assert not returns_to_available(opened, 20, other_tasks_in_progress=False)


def test_car_of_an_open_incident_comes_back_by_repair_only() -> None:
    assert not returns_to_available(
        state(90),
        20,
        other_tasks_in_progress=False,
        held_by_incident=True,
    )
    assert returns_to_available(
        state(90),
        20,
        other_tasks_in_progress=False,
        held_by_incident=True,
        repaired=True,
    )
