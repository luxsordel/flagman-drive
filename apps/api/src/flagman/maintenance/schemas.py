from typing import Annotated, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import Car, CarStatus
from flagman.maintenance.domain import (
    COMMENT_MAX_LENGTH,
    COMMENT_MIN_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    PAGE_SIZE_MAX,
    MaintenanceCommand,
    Task,
    TaskFilter,
    TaskStatus,
    TaskType,
)
from flagman.maintenance.service import TaskDetails, TaskPage, TaskView
from flagman.rentals.domain import normalize_damage_comment

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


def _check_text(text: str) -> str:
    # Control and format characters would make the text read differently in the panel.
    return normalize_damage_comment(text)


class TaskCarOut(BaseModel):
    """What a technician needs to find the car and see its charge."""

    id: UUID
    plate: str
    brand: str
    model: str
    status: CarStatus
    lat: float | None
    lon: float | None
    battery_pct: int | None
    recorded_at: AwareDatetime | None

    @classmethod
    def of(cls, car: Car) -> Self:
        state = car.state
        return cls(
            id=car.id,
            plate=car.plate,
            brand=car.model.brand,
            model=car.model.name,
            status=car.status,
            lat=state.location.lat if state else None,
            lon=state.location.lon if state else None,
            battery_pct=state.battery_pct if state else None,
            recorded_at=state.recorded_at if state else None,
        )


class TaskOut(BaseModel):
    id: UUID
    type: TaskType
    status: TaskStatus
    description: str | None
    assignee_id: UUID | None
    created_by: UUID | None = Field(description="Empty for a task the system created.")
    comment: str | None = Field(description="Left on completion or cancellation.")
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    car: TaskCarOut

    @classmethod
    def of(cls, view: TaskView) -> Self:
        return cls.of_task(view.task, view.car)

    @classmethod
    def of_task(cls, task: Task, car: Car) -> Self:
        return cls(
            id=task.id,
            type=task.type,
            status=task.status,
            description=task.description,
            assignee_id=task.assignee_id,
            created_by=task.created_by,
            comment=task.comment,
            created_at=task.created_at,
            started_at=task.started_at,
            finished_at=task.finished_at,
            car=TaskCarOut.of(car),
        )


class TaskPhotoOut(BaseModel):
    id: UUID
    uploaded_at: AwareDatetime


class TaskDetailsOut(TaskOut):
    photos: list[TaskPhotoOut]

    @classmethod
    def of_details(cls, details: TaskDetails) -> Self:
        return cls(
            **TaskOut.of(details.view).model_dump(),
            photos=[TaskPhotoOut(id=p.id, uploaded_at=p.uploaded_at) for p in details.photos],
        )


class TaskList(BaseModel):
    items: list[TaskOut] = Field(description="Newest first.")
    total: int

    @classmethod
    def of(cls, page: TaskPage) -> Self:
        return cls(items=[TaskOut.of(view) for view in page.items], total=page.total)


class TasksQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: list[TaskStatus] = Field(default_factory=list, description="Any of these.")
    type: list[TaskType] = Field(default_factory=list, description="Any of these.")
    car_id: UUID | None = None
    assignee_id: UUID | None = None
    free: bool = Field(default=False, description="New tasks without an assignee only.")
    limit: int = Field(default=50, ge=1, le=PAGE_SIZE_MAX)
    offset: int = Field(default=0, ge=0)

    def filter(self) -> TaskFilter:
        return TaskFilter(
            statuses=frozenset(self.status),
            types=frozenset(self.type),
            car_id=self.car_id,
            assignee_id=self.assignee_id,
            free_only=self.free,
        )


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    car_id: UUID
    type: TaskType
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX_LENGTH)
    assignee_id: UUID | None = Field(default=None, description="An active technician.")

    @field_validator("description")
    @classmethod
    def _check_description(cls, description: str | None) -> str | None:
        return _check_text(description) if description is not None else None


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: UUID = Field(description="An active technician.")


class CompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str = Field(
        min_length=COMMENT_MIN_LENGTH,
        max_length=COMMENT_MAX_LENGTH,
        description="What was done; staff read it next to the photos.",
    )

    @field_validator("comment")
    @classmethod
    def _check_comment(cls, comment: str) -> str:
        comment = _check_text(comment)
        if len(comment) < COMMENT_MIN_LENGTH:
            raise ValueError(f"the comment needs at least {COMMENT_MIN_LENGTH} characters")
        return comment


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: str | None = Field(default=None, max_length=COMMENT_MAX_LENGTH)

    @field_validator("comment")
    @classmethod
    def _check_comment(cls, comment: str | None) -> str | None:
        return _check_text(comment) if comment is not None else None


class TaskCommandIn(BaseModel):
    """Where the technician is right now, for the distance to the car; never logged or stored."""

    model_config = ConfigDict(extra="forbid")

    command: MaintenanceCommand
    lat: Latitude
    lon: Longitude

    def point(self) -> GeoPoint:
        return GeoPoint(lat=self.lat, lon=self.lon)


class TaskCommandOut(BaseModel):
    command_id: UUID
    task: TaskOut


class TechnicianOut(BaseModel):
    id: UUID
    email: str


class TechnicianList(BaseModel):
    items: list[TechnicianOut] = Field(description="Active technicians by email.")


class PhotoUrlOut(BaseModel):
    url: str = Field(description="Opens the photo without a token; do not store or share it.")
    expires_in: int = Field(description="Seconds the link keeps working.", examples=[300])


class TaskPhotosOut(BaseModel):
    photos: list[TaskPhotoOut]
