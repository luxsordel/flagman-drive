import uuid
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flagman.core.config import Settings
from flagman.fleet.domain import CarCommand, CarStatus
from flagman.fleet.tests.conftest import FakeGateway, Staff, add_car, run_in_db
from flagman.identity.domain import StaffRole
from flagman.maintenance import service
from flagman.maintenance.domain import ChargingRules
from flagman.maintenance.tests.conftest import Member
from flagman.rentals.tests.conftest import (
    CAR_POINT,
    FAR_FROM_CAR,
    FakeStorage,
    car_status,
    execute,
    photo,
    problem_type,
    query,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def operator(integration_settings: Settings, app_client: TestClient) -> Member:
    return Member(integration_settings, app_client, StaffRole.OPERATOR)


@pytest.fixture
def technician(integration_settings: Settings, app_client: TestClient) -> Member:
    return Member(integration_settings, app_client, StaffRole.TECHNICIAN)


@pytest.fixture
def colleague(integration_settings: Settings, app_client: TestClient) -> Member:
    return Member(integration_settings, app_client, StaffRole.TECHNICIAN)


@pytest.fixture
def car(integration_settings: Settings) -> str:
    return add_car(integration_settings, CAR_POINT, battery_pct=80)


def created(response: Any) -> str:
    assert response.status_code == HTTPStatus.CREATED, response.text
    return str(response.json()["id"])


def events(settings: Settings, task_id: str) -> list[str]:
    rows = query(
        settings,
        "SELECT action FROM audit_events WHERE target_id = :id ORDER BY occurred_at",
        id=uuid.UUID(task_id),
    )
    return [row.action for row in rows]


def set_battery(settings: Settings, car_id: str, battery_pct: int) -> None:
    execute(
        settings,
        "UPDATE cars SET battery_pct = :pct WHERE id = :id",
        pct=battery_pct,
        id=uuid.UUID(car_id),
    )


# Creating and assigning


def test_operator_creates_task_and_it_is_audited(
    integration_settings: Settings, operator: Member, car: str
) -> None:
    response = operator.create(car, "washing", description="  Dirty   seats ")
    task_id = created(response)
    body = response.json()
    assert body["status"] == "new"
    assert body["description"] == "Dirty seats"
    assert body["created_by"] == operator.id
    assert body["car"]["id"] == car
    assert events(integration_settings, task_id) == ["maintenance_task.created"]


def test_task_created_with_assignee_is_assigned(
    operator: Member, technician: Member, car: str
) -> None:
    body = operator.create(car, "repair", assignee_id=technician.id).json()
    assert body["status"] == "assigned"
    assert body["assignee_id"] == technician.id


@pytest.mark.parametrize("role", [StaffRole.OPERATOR, StaffRole.ADMIN])
def test_assignee_must_be_active_technician(
    integration_settings: Settings, operator: Member, car: str, role: StaffRole
) -> None:
    not_technician = Staff(integration_settings, role)
    response = operator.create(car, assignee_id=str(not_technician.id))
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert problem_type(response) == "maintenance-assignee-invalid"


def test_disabled_technician_is_not_assignable(
    operator: Member, technician: Member, car: str
) -> None:
    technician.staff.disable()
    response = operator.create(car, assignee_id=technician.id)
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_second_open_task_of_same_type_is_refused(operator: Member, car: str) -> None:
    created(operator.create(car, "washing"))
    response = operator.create(car, "washing")
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "maintenance-task-duplicate"
    created(operator.create(car, "repair"))


def test_task_for_unknown_car_is_not_found(operator: Member) -> None:
    response = operator.create(str(uuid.uuid4()))
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert problem_type(response) == "car-not-found"


def test_technician_does_not_create_assign_or_cancel(
    operator: Member, technician: Member, car: str
) -> None:
    task_id = created(operator.create(car))
    assert technician.create(car, "repair").status_code == HTTPStatus.FORBIDDEN
    assign = technician.post(f"/tasks/{task_id}/assign", {"assignee_id": technician.id})
    assert assign.status_code == HTTPStatus.FORBIDDEN
    assert technician.post(f"/tasks/{task_id}/cancel").status_code == HTTPStatus.FORBIDDEN
    assert technician.get("/technicians").status_code == HTTPStatus.FORBIDDEN


def test_operator_reassigns_before_start(
    integration_settings: Settings,
    operator: Member,
    technician: Member,
    colleague: Member,
    car: str,
) -> None:
    task_id = created(operator.create(car, assignee_id=technician.id))
    response = operator.post(f"/tasks/{task_id}/assign", {"assignee_id": colleague.id})
    assert response.status_code == HTTPStatus.OK
    assert response.json()["assignee_id"] == colleague.id
    assert events(integration_settings, task_id)[-1] == "maintenance_task.assigned"


def test_technicians_list_has_active_technicians_only(
    operator: Member, technician: Member, colleague: Member
) -> None:
    colleague.staff.disable()
    ids = {item["id"] for item in operator.get("/technicians").json()["items"]}
    assert technician.id in ids
    assert colleague.id not in ids
    assert operator.id not in ids


# What a technician sees


def test_technician_sees_free_and_own_tasks_only(
    operator: Member, technician: Member, colleague: Member, car: str
) -> None:
    free = created(operator.create(car, "washing"))
    own = created(operator.create(car, "repair", assignee_id=technician.id))
    other = created(operator.create(car, "inspection", assignee_id=colleague.id))
    ids = {item["id"] for item in technician.get("/tasks", car_id=car).json()["items"]}
    assert ids == {free, own}
    assert technician.get(f"/tasks/{other}").status_code == HTTPStatus.NOT_FOUND
    free_ids = {
        item["id"] for item in technician.get("/tasks", car_id=car, free=True).json()["items"]
    }
    assert free_ids == {free}


def test_operator_lists_with_filters(operator: Member, technician: Member, car: str) -> None:
    created(operator.create(car, "washing"))
    own = created(operator.create(car, "repair", assignee_id=technician.id))
    body = operator.get("/tasks", car_id=car, type=["repair"], status=["assigned"]).json()
    assert [item["id"] for item in body["items"]] == [own]
    assert body["total"] == 1
    assert operator.get("/tasks", car_id=car, assignee_id=technician.id).json()["total"] == 1


# Taking a task, the car in service


def test_taking_task_sends_car_to_service(
    integration_settings: Settings, operator: Member, technician: Member, car: str
) -> None:
    task_id = created(operator.create(car))
    response = technician.start(task_id)
    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["assignee_id"] == technician.id
    assert body["car"]["status"] == "service"
    assert car_status(integration_settings, car) == "service"
    assert events(integration_settings, task_id)[-1] == "maintenance_task.started"


def test_technician_does_not_take_colleagues_task(
    operator: Member, technician: Member, colleague: Member, car: str
) -> None:
    task_id = created(operator.create(car, assignee_id=colleague.id))
    assert technician.start(task_id).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("status", [CarStatus.RESERVED, CarStatus.IN_RENTAL])
def test_busy_car_is_not_taken(
    integration_settings: Settings, operator: Member, technician: Member, status: CarStatus
) -> None:
    busy = add_car(integration_settings, CAR_POINT, status=status)
    task_id = created(operator.create(busy))
    response = technician.start(task_id)
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "maintenance-car-busy"
    assert car_status(integration_settings, busy) == status.value


def test_started_task_is_not_taken_again(operator: Member, technician: Member, car: str) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = operator.start(task_id)
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "maintenance-task-transition-not-allowed"


# Completing and cancelling


def test_completing_returns_charged_car_to_clients(
    integration_settings: Settings, operator: Member, technician: Member, car: str
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = technician.complete(task_id)
    assert response.status_code == HTTPStatus.OK, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["comment"] == "Washed inside and out"
    assert car_status(integration_settings, car) == "available"
    assert events(integration_settings, task_id)[-1] == "maintenance_task.completed"


def test_car_low_on_charge_stays_in_service(
    integration_settings: Settings, operator: Member, technician: Member
) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=10)
    task_id = created(operator.create(low, "washing"))
    technician.start(task_id)
    assert technician.complete(task_id).status_code == HTTPStatus.OK
    assert car_status(integration_settings, low) == "service"


def test_car_stays_in_service_while_another_task_is_in_progress(
    integration_settings: Settings,
    operator: Member,
    technician: Member,
    colleague: Member,
    car: str,
) -> None:
    washing = created(operator.create(car, "washing"))
    repair = created(operator.create(car, "repair"))
    technician.start(washing)
    colleague.start(repair)
    technician.complete(washing)
    assert car_status(integration_settings, car) == "service"
    colleague.complete(repair, "Mirror replaced")
    assert car_status(integration_settings, car) == "available"


def test_charging_task_waits_for_the_charge(
    integration_settings: Settings, operator: Member, technician: Member
) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=12)
    task_id = created(operator.create(low, "charging"))
    technician.start(task_id)
    response = technician.complete(task_id, "Charged")
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "maintenance-charge-too-low"
    set_battery(integration_settings, low, 90)
    assert technician.complete(task_id, "Charged").status_code == HTTPStatus.OK
    assert car_status(integration_settings, low) == "available"


def test_only_assignee_completes(
    operator: Member, technician: Member, colleague: Member, car: str
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    assert colleague.complete(task_id).status_code == HTTPStatus.NOT_FOUND
    response = operator.complete(task_id)
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "maintenance-task-not-in-progress"


def test_completion_needs_comment(operator: Member, technician: Member, car: str) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    assert technician.complete(task_id, "  ").status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_cancelling_task_in_progress_returns_car(
    integration_settings: Settings, operator: Member, technician: Member, car: str
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = operator.post(f"/tasks/{task_id}/cancel", {"comment": "Not needed"})
    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "cancelled"
    assert car_status(integration_settings, car) == "available"
    assert events(integration_settings, task_id)[-1] == "maintenance_task.cancelled"
    # The type is free again for a new task.
    created(operator.create(car))


def test_cancelling_new_task_leaves_car_alone(
    integration_settings: Settings, operator: Member, car: str
) -> None:
    execute(
        integration_settings, "UPDATE cars SET status = 'offline' WHERE id = :id", id=uuid.UUID(car)
    )
    task_id = created(operator.create(car))
    assert operator.post(f"/tasks/{task_id}/cancel").status_code == HTTPStatus.OK
    assert car_status(integration_settings, car) == "offline"


# Service access


def test_technician_opens_car_of_own_task_nearby(
    integration_settings: Settings,
    operator: Member,
    technician: Member,
    car: str,
    gateway: FakeGateway,
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = technician.command(task_id, "open")
    assert response.status_code == HTTPStatus.OK, response.text
    assert [call[2] for call in gateway.calls] == [CarCommand.SERVICE_OPEN]
    rows = query(
        integration_settings,
        "SELECT actor_id, details FROM audit_events "
        "WHERE action = 'car.command_sent' AND target_id = :car",
        car=uuid.UUID(car),
    )
    assert str(rows[-1].actor_id) == technician.id
    assert rows[-1].details["maintenance_task_id"] == task_id


def test_far_technician_does_not_open(
    operator: Member, technician: Member, car: str, gateway: FakeGateway
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = technician.command(task_id, "open", lat=FAR_FROM_CAR.lat)
    assert response.status_code == HTTPStatus.CONFLICT
    assert problem_type(response) == "too-far-from-car"
    assert gateway.calls == []


def test_stale_telemetry_refuses_service_access(
    integration_settings: Settings, operator: Member, technician: Member, gateway: FakeGateway
) -> None:
    stale = add_car(integration_settings, CAR_POINT, age=timedelta(minutes=30))
    task_id = created(operator.create(stale))
    technician.start(task_id)
    response = technician.command(task_id, "open")
    assert problem_type(response) == "car-location-unknown"
    assert gateway.calls == []


def test_no_service_access_before_start_or_to_colleagues(
    operator: Member, technician: Member, colleague: Member, car: str, gateway: FakeGateway
) -> None:
    task_id = created(operator.create(car, assignee_id=technician.id))
    assert problem_type(technician.command(task_id, "open")) == "maintenance-task-not-in-progress"
    technician.start(task_id)
    assert colleague.command(task_id, "open").status_code == HTTPStatus.NOT_FOUND
    assert gateway.calls == []


def test_charging_commands_on_charging_task(
    integration_settings: Settings, operator: Member, technician: Member, gateway: FakeGateway
) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=10)
    charging = created(operator.create(low, "charging"))
    washing = created(operator.create(low, "washing"))
    technician.start(charging)
    assert technician.command(charging, "start_charging").status_code == HTTPStatus.OK
    assert gateway.calls[-1][2] is CarCommand.START_CHARGING
    operator.post(f"/tasks/{washing}/assign", {"assignee_id": technician.id})
    technician.start(washing)
    response = technician.command(washing, "start_charging")
    assert problem_type(response) == "maintenance-command-not-for-task"


def test_technician_has_no_manual_fleet_commands(
    app_client: TestClient, technician: Member, car: str, gateway: FakeGateway
) -> None:
    response = app_client.post(
        f"/admin/fleet/cars/{car}/commands",
        json={"command": "open", "comment": "Checking the car"},
        headers=technician.staff.headers,
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert gateway.calls == []


def test_manual_fleet_commands_refuse_charging(
    app_client: TestClient, operator: Member, car: str, gateway: FakeGateway
) -> None:
    response = app_client.post(
        f"/admin/fleet/cars/{car}/commands",
        json={"command": "start_charging", "comment": "Plug it in"},
        headers=operator.staff.headers,
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert gateway.calls == []


def test_service_access_needs_live_session(
    operator: Member, technician: Member, car: str, gateway: FakeGateway
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    technician.staff.revoke_session()
    assert technician.command(task_id, "open").status_code == HTTPStatus.UNAUTHORIZED
    assert gateway.calls == []


# Photos


def test_assignee_adds_photos_and_staff_get_short_links(
    operator: Member, technician: Member, car: str, storage: FakeStorage
) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = technician.upload(task_id, photo(1))
    assert response.status_code == HTTPStatus.OK, response.text
    photo_id = response.json()["photos"][0]["id"]
    assert list(storage.objects) == [next(iter(storage.objects))]
    assert next(iter(storage.objects)).startswith(f"maintenance/{task_id}/")
    link = operator.get(f"/tasks/{task_id}/photos/{photo_id}/url")
    assert link.status_code == HTTPStatus.OK
    assert link.headers["Cache-Control"] == "no-store"
    assert link.json()["expires_in"] == 300
    details = operator.get(f"/tasks/{task_id}").json()
    assert [item["id"] for item in details["photos"]] == [photo_id]


def test_photos_only_to_own_task_in_progress(
    operator: Member, technician: Member, colleague: Member, car: str, storage: FakeStorage
) -> None:
    task_id = created(operator.create(car, assignee_id=technician.id))
    assert problem_type(technician.upload(task_id, photo())) == "maintenance-task-not-in-progress"
    technician.start(task_id)
    assert colleague.upload(task_id, photo()).status_code == HTTPStatus.NOT_FOUND
    assert storage.objects == {}


def test_photo_limit(operator: Member, technician: Member, car: str) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    for seed in range(8):
        assert technician.upload(task_id, photo(seed)).status_code == HTTPStatus.OK
    assert problem_type(technician.upload(task_id, photo(9))) == "maintenance-task-photo-limit"


def test_not_a_photo_is_refused(operator: Member, technician: Member, car: str) -> None:
    task_id = created(operator.create(car))
    technician.start(task_id)
    response = technician.upload(task_id, b"not an image")
    assert response.status_code == HTTPStatus.UNSUPPORTED_MEDIA_TYPE


# The charging task job


def run_job(settings: Settings, threshold_pct: int = 20) -> int:
    rules = ChargingRules(threshold_pct=threshold_pct, max_state_age=timedelta(minutes=5))
    return run_in_db(
        settings, lambda db: service.create_charging_tasks(db, rules, datetime.now(UTC))
    )


def open_tasks(settings: Settings, car_id: str) -> list[Any]:
    return query(
        settings,
        "SELECT type, status, created_by FROM maintenance_tasks "
        "WHERE car_id = :car AND status IN ('new', 'assigned', 'in_progress')",
        car=uuid.UUID(car_id),
    )


def test_job_creates_one_charging_task_for_low_free_car(integration_settings: Settings) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=15)
    run_job(integration_settings)
    run_job(integration_settings)
    rows = open_tasks(integration_settings, low)
    assert [(row.type, row.status, row.created_by) for row in rows] == [("charging", "new", None)]
    actors = query(
        integration_settings,
        "SELECT actor_type, details FROM audit_events WHERE action = 'maintenance_task.created' "
        "AND details->>'car_id' = :car",
        car=low,
    )
    assert [row.actor_type for row in actors] == ["system"]
    assert actors[0].details["battery_pct"] == 15


@pytest.mark.parametrize(
    ("status", "battery_pct", "age"),
    [
        (CarStatus.AVAILABLE, 20, timedelta(seconds=5)),
        (CarStatus.AVAILABLE, 10, timedelta(minutes=30)),
        (CarStatus.RESERVED, 5, timedelta(seconds=5)),
        (CarStatus.IN_RENTAL, 5, timedelta(seconds=5)),
        (CarStatus.SERVICE, 5, timedelta(seconds=5)),
    ],
)
def test_job_leaves_other_cars_alone(
    integration_settings: Settings, status: CarStatus, battery_pct: int, age: timedelta
) -> None:
    other = add_car(
        integration_settings, CAR_POINT, status=status, battery_pct=battery_pct, age=age
    )
    run_job(integration_settings)
    assert open_tasks(integration_settings, other) == []


def test_job_skips_car_with_any_open_task(integration_settings: Settings, operator: Member) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=10)
    created(operator.create(low, "washing"))
    run_job(integration_settings)
    assert [row.type for row in open_tasks(integration_settings, low)] == ["washing"]


def test_charging_cycle(
    integration_settings: Settings, technician: Member, gateway: FakeGateway
) -> None:
    low = add_car(integration_settings, CAR_POINT, battery_pct=10)
    run_job(integration_settings)
    task_id = next(
        item["id"] for item in technician.get("/tasks", car_id=low, free=True).json()["items"]
    )
    technician.start(task_id)
    assert car_status(integration_settings, low) == "service"
    technician.command(task_id, "start_charging")
    set_battery(integration_settings, low, 95)
    technician.command(task_id, "stop_charging")
    assert technician.complete(task_id, "Charged to 95%").status_code == HTTPStatus.OK
    assert car_status(integration_settings, low) == "available"
    assert [call[2] for call in gateway.calls] == [
        CarCommand.START_CHARGING,
        CarCommand.STOP_CHARGING,
    ]
