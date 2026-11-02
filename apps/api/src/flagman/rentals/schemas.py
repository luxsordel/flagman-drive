from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import range_left_km
from flagman.pricing.domain import IntervalKind, Quote
from flagman.rentals.domain import (
    DAMAGE_COMMENT_MAX_LENGTH,
    MAX_INSPECTION_PHOTOS,
    MIN_INSPECTION_PHOTOS,
    CancelReason,
    ChargeStatus,
    DamageSide,
    Inspection,
    InspectionStage,
    Receipt,
    RentalCommand,
    RentalStatus,
    TripWarning,
    normalize_damage_comment,
)
from flagman.rentals.service import Debt, RentalView

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


class CostLineOut(BaseModel):
    """Amounts in kopecks of BYN."""

    kind: IntervalKind
    minutes: int = Field(description="Billed minutes, after the free ones; a started one counts.")
    free_minutes: int
    per_minute: int
    amount: int


class CostOut(BaseModel):
    """Counted by the server only; the client shows it and never sends it back."""

    lines: list[CostLineOut]
    total: int

    @classmethod
    def of(cls, quote: Quote) -> "CostOut":
        return cls(
            lines=[
                CostLineOut(
                    kind=line.kind,
                    minutes=line.minutes,
                    free_minutes=line.free_minutes,
                    per_minute=line.per_minute,
                    amount=line.amount,
                )
                for line in quote.lines
            ],
            total=quote.total,
        )


class ReceiptOut(BaseModel):
    """Issued once, when the rental ends; it never changes afterwards."""

    rental_id: UUID
    tariff_version_id: UUID
    cost: CostOut
    card_brand: str | None
    card_last4: str | None = Field(description="The card the money went to, as «•••• 4242».")
    issued_at: datetime

    @classmethod
    def of(cls, receipt: Receipt) -> "ReceiptOut":
        return cls(
            rental_id=receipt.rental_id,
            tariff_version_id=receipt.tariff_version_id,
            cost=CostOut.of(receipt.quote),
            card_brand=receipt.card.brand if receipt.card else None,
            card_last4=receipt.card.last4 if receipt.card else None,
            issued_at=receipt.issued_at,
        )


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
    after_inspection_submitted: bool = Field(
        description="The inspection after the trip is done: the rental may be finished."
    )
    cost: CostOut = Field(description="So far, by the server's clock; the receipt's once ended.")
    warnings: list[TripWarning] = Field(
        description="`low_battery`, `low_range`: the range may not last; only during a trip."
    )
    charge_status: ChargeStatus | None = Field(
        description="`pending`: being paid; `paid`; `failed`: a debt. Null: nothing to pay."
    )
    receipt: ReceiptOut | None
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
            after_inspection_submitted=view.after_inspection_submitted,
            cost=CostOut.of(view.cost),
            warnings=list(view.warnings),
            charge_status=rental.charge_status,
            receipt=ReceiptOut.of(view.receipt) if view.receipt else None,
            as_of=now,
        )


class CurrentRentalOut(BaseModel):
    rental: RentalOut | None = Field(description="Null when the client has no active rental.")


class DebtOut(BaseModel):
    rental_id: UUID
    amount: int = Field(description="Still owed, in kopecks of BYN.")
    ended_at: datetime | None

    @classmethod
    def of(cls, debt: Debt) -> "DebtOut":
        return cls(rental_id=debt.rental_id, amount=debt.amount, ended_at=debt.ended_at)


class DebtsOut(BaseModel):
    debts: list[DebtOut] = Field(description="Empty when the client owes nothing.")


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
