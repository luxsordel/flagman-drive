import uuid
from datetime import timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status

from car_emulator.domain import CommandRejectedError
from car_emulator.fleet import CarNotFoundError, DuplicateCommandError, FleetService
from car_emulator.ports import Clock
from car_emulator.schemas import CarState, CommandRequest, CommandResult, Health

router = APIRouter()
# Only with EMULATOR_DEMO_ENDPOINTS: signed like every other route, never part of a real provider.
demo_router = APIRouter(prefix="/demo")

HEALTH_PATH = "/health"


def get_fleet(request: Request) -> FleetService:
    return cast(FleetService, request.app.state.fleet)


def get_clock(request: Request) -> Clock:
    return cast(Clock, request.app.state.clock)


def get_demo_rest(request: Request) -> timedelta:
    return cast(timedelta, request.app.state.demo_rest)


Fleet = Annotated[FleetService, Depends(get_fleet)]
ClockDep = Annotated[Clock, Depends(get_clock)]


@router.get(HEALTH_PATH)
async def health() -> Health:
    return Health(status="ok")


@router.get("/cars")
async def list_cars(fleet: Fleet) -> list[CarState]:
    return [CarState.from_domain(car) for car in fleet.list_cars()]


@router.get("/cars/{car_id}")
async def get_car(car_id: uuid.UUID, fleet: Fleet) -> CarState:
    try:
        return CarState.from_domain(fleet.get_car(car_id))
    except CarNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "car_not_found") from None


@router.post("/cars/{car_id}/commands")
async def execute_command(
    car_id: uuid.UUID, command: CommandRequest, fleet: Fleet, clock: ClockDep
) -> CommandResult:
    try:
        car = fleet.execute(car_id, command.command_id, command.type, clock())
    except CarNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "car_not_found") from None
    except DuplicateCommandError:
        raise HTTPException(status.HTTP_409_CONFLICT, "duplicate_command") from None
    except CommandRejectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"command_rejected:{exc.reason}") from None
    return CommandResult(command_id=command.command_id, car=CarState.from_domain(car))


@demo_router.post("/cars/{car_id}/park")
async def park_for_demo(
    car_id: uuid.UUID,
    fleet: Fleet,
    clock: ClockDep,
    rest: Annotated[timedelta, Depends(get_demo_rest)],
) -> CarState:
    """Stops the rented car with the ignition off, so the rental can be finished now."""
    try:
        return CarState.from_domain(fleet.park_for_demo(car_id, rest, clock()))
    except CarNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "car_not_found") from None
    except CommandRejectedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"command_rejected:{exc.reason}") from None
