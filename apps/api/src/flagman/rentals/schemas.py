from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import range_left_km
from flagman.rentals.domain import (
    DAMAGE_COMMENT_MAX_LENGTH,
    MAX_INSPECTION_PHOTOS,
    MIN_INSPECTION_PHOTOS,
    CancelReason,
    DamageSide,
    Inspection,
    InspectionStage,
    RentalCommand,
    RentalStatus,
    normalize_damage_comment,
)
from flagman.rentals.service import RentalView

Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]


class ReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    car_id: UUID


class RentalCarOut(BaseModel):
    """The reserved car as the client needs it to find it: the plate is shown from now on."""

    id: UUID
    brand: str
    model: str
    plate: str
    photo_url: str | None
    lat: float | None = Field(description="Where the car last reported itself; null if unknown.")
    lon: float | None
    battery_pct: int | None
    range_km: int | None = Field(description="Estimated from the charge, rounded down.")
    doors_locked: bool | None


class RentalTariffOut(BaseModel):
    """Prices of the tariff version fixed at the reservation, in kopecks of BYN."""

    driving_per_minute: int
    parked_per_minute: int
    waiting_per_minute: int = Field(description="The paid wait, after the free minutes.")


class RentalOut(BaseModel):
    id: UUID
    status: RentalStatus
    car: RentalCarOut
    tariff: RentalTariffOut
    reserved_at: datetime
    free_wait_until: datetime = Field(description="The reservation is free until then.")
    expires_at: datetime = Field(
        description="Then the reservation is cancelled unless the car has been opened."
    )
    trip_started_at: datetime | None
    ended_at: datetime | None
    cancel_reason: CancelReason | None
    inspection_submitted: bool = Field(
        description="The inspection before the trip is done: the car may be opened."
    )
    as_of: datetime = Field(description="Server time: count the deadlines down from it.")

    @classmethod
    def of(cls, view: RentalView, now: datetime) -> "RentalOut":
        rental, car, state = view.rental, view.car, view.car.state
        return cls(
            id=rental.id,
            status=rental.status,
            car=RentalCarOut(
                id=car.id,
                brand=car.model.brand,
                model=car.model.name,
                plate=car.plate,
                photo_url=car.model.photo_url,
                lat=state.location.lat if state else None,
                lon=state.location.lon if state else None,
                battery_pct=state.battery_pct if state else None,
                range_km=range_left_km(car.model.range_km, state.battery_pct) if state else None,
                doors_locked=state.doors_locked if state else None,
            ),
            tariff=RentalTariffOut(
                driving_per_minute=view.prices.driving_per_minute,
                parked_per_minute=view.prices.parked_per_minute,
                waiting_per_minute=view.prices.waiting_per_minute,
            ),
            reserved_at=rental.reserved_at,
            free_wait_until=rental.free_wait_until,
            expires_at=rental.expires_at,
            trip_started_at=rental.trip_started_at,
            ended_at=rental.ended_at,
            cancel_reason=rental.cancel_reason,
            inspection_submitted=view.inspection_submitted,
            as_of=now,
        )


class CurrentRentalOut(BaseModel):
    rental: RentalOut | None = Field(description="Null when the client has no active rental.")


class InspectionPhotoOut(BaseModel):
    id: UUID
    uploaded_at: datetime


class DamageOut(BaseModel):
    id: UUID
    side: DamageSide
    comment: str
    created_at: datetime


class InspectionOut(BaseModel):
    """No links here: the client asks for one only when it shows the photo."""

    stage: InspectionStage
    photos: list[InspectionPhotoOut]
    damages: list[DamageOut]
    submitted_at: datetime | None
    min_photos: int = Field(examples=[MIN_INSPECTION_PHOTOS])
    max_photos: int = Field(examples=[MAX_INSPECTION_PHOTOS])

    @classmethod
    def of(cls, inspection: Inspection) -> "InspectionOut":
        return cls(
            stage=inspection.stage,
            photos=[
                InspectionPhotoOut(id=p.id, uploaded_at=p.uploaded_at) for p in inspection.photos
            ],
            damages=[
                DamageOut(id=d.id, side=d.side, comment=d.comment, created_at=d.created_at)
                for d in inspection.damages
            ],
            submitted_at=inspection.submitted_at,
            min_photos=MIN_INSPECTION_PHOTOS,
            max_photos=MAX_INSPECTION_PHOTOS,
        )


def _non_empty_comment(comment: str) -> str:
    normalized = normalize_damage_comment(comment)
    if not normalized:
        raise ValueError("describe the damage")
    if len(normalized) > DAMAGE_COMMENT_MAX_LENGTH:
        raise ValueError(f"at most {DAMAGE_COMMENT_MAX_LENGTH} characters")
    return normalized


class DamageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: DamageSide
    # The hard limit is checked after the spaces are squeezed; this one stops a huge string.
    comment: Annotated[
        str, Field(max_length=2 * DAMAGE_COMMENT_MAX_LENGTH), AfterValidator(_non_empty_comment)
    ] = Field(examples=["Scratch on the rear bumper"])


class CommandIn(BaseModel):
    """Where the client is right now, for the distance to the car; never logged or stored."""

    model_config = ConfigDict(extra="forbid")

    command: RentalCommand
    lat: Latitude
    lon: Longitude

    def point(self) -> GeoPoint:
        return GeoPoint(lat=self.lat, lon=self.lon)


class PhotoUrlOut(BaseModel):
    url: str = Field(description="Opens the photo without a token; do not store or share it.")
    expires_in: int = Field(description="Seconds the link keeps working.", examples=[300])
