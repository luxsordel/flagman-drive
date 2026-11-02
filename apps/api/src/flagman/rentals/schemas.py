from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from flagman.core.geo import GeoPoint
from flagman.fleet.domain import range_left_km
from flagman.identity.service import ClientIdentity
from flagman.payments.domain import (
    DeclineReason,
    OperationKind,
    OperationStatus,
    PaymentOperation,
)
from flagman.pricing.domain import IntervalKind, Quote
from flagman.rentals.domain import (
    DAMAGE_COMMENT_MAX_LENGTH,
    MAX_INSPECTION_PHOTOS,
    MIN_INSPECTION_PHOTOS,
    STAFF_REASON_MAX_LENGTH,
    Adjustment,
    CancelReason,
    ChargeStatus,
    DamageSide,
    Inspection,
    InspectionStage,
    Receipt,
    RentalCommand,
    RentalCursor,
    RentalFilter,
    RentalStatus,
    TripWarning,
    normalize_damage_comment,
    normalize_staff_text,
)
from flagman.rentals.service import (
    HISTORY_PAGE_DEFAULT,
    HISTORY_PAGE_MAX,
    Debt,
    HistoryEntry,
    HistoryPage,
    RentalView,
    StaffRentalCard,
    StaffRentalPage,
)

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


class PointOut(BaseModel):
    lat: float
    lon: float

    @classmethod
    def of(cls, point: GeoPoint | None) -> "PointOut | None":
        return cls(lat=point.lat, lon=point.lon) if point else None


class AdjustmentOut(BaseModel):
    """A staff change of the total; the receipt keeps the original amount."""

    id: UUID
    amount: int = Field(description="Kopecks of BYN; negative lowers the total.")
    created_at: datetime

    @classmethod
    def of(cls, adjustment: Adjustment) -> "AdjustmentOut":
        return cls(id=adjustment.id, amount=adjustment.amount, created_at=adjustment.created_at)


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
    adjustments: list[AdjustmentOut] = Field(description="Oldest first; empty without any.")
    total: int = Field(description="`cost.total` with the adjustments, in kopecks of BYN.")
    start: PointOut | None = Field(description="Where the car stood when the trip started.")
    end: PointOut | None = Field(description="Where the car stood when the trip ended.")
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
            adjustments=[AdjustmentOut.of(adjustment) for adjustment in view.adjustments],
            total=view.total,
            start=PointOut.of(view.ends.start),
            end=PointOut.of(view.ends.end),
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


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cursor: str | None = Field(
        default=None, max_length=200, description="`next_cursor` of the previous page."
    )
    limit: int = Field(default=HISTORY_PAGE_DEFAULT, ge=1, le=HISTORY_PAGE_MAX)

    @field_validator("cursor")
    @classmethod
    def _check_cursor(cls, value: str | None) -> str | None:
        if value is not None:
            RentalCursor.decode(value)
        return value

    def position(self) -> RentalCursor | None:
        return RentalCursor.decode(self.cursor) if self.cursor else None


class HistoryCarOut(BaseModel):
    id: UUID
    brand: str
    model: str
    plate: str
    photo_url: str | None


class HistoryItemOut(BaseModel):
    id: UUID
    status: RentalStatus
    car: HistoryCarOut
    reserved_at: datetime
    trip_started_at: datetime | None
    ended_at: datetime | None
    total: int = Field(description="With the staff adjustments, in kopecks of BYN.")
    charge_status: ChargeStatus | None = Field(
        description="`pending`: being paid; `paid`; `failed`: a debt. Null: nothing to pay."
    )

    @classmethod
    def of(cls, entry: HistoryEntry) -> "HistoryItemOut":
        rental, car = entry.rental, entry.car
        return cls(
            id=rental.id,
            status=rental.status,
            car=HistoryCarOut(
                id=car.id,
                brand=car.model.brand,
                model=car.model.name,
                plate=car.plate,
                photo_url=car.model.photo_url,
            ),
            reserved_at=rental.reserved_at,
            trip_started_at=rental.trip_started_at,
            ended_at=rental.ended_at,
            total=entry.total,
            charge_status=rental.charge_status,
        )


class HistoryPageOut(BaseModel):
    items: list[HistoryItemOut] = Field(description="Newest reservation first.")
    next_cursor: str | None = Field(description="Absent on the last page.")

    @classmethod
    def of(cls, page: HistoryPage) -> "HistoryPageOut":
        return cls(
            items=[HistoryItemOut.of(entry) for entry in page.entries],
            next_cursor=page.next_cursor.encode() if page.next_cursor else None,
        )


# Staff (docs/adr/0025)

# A correction, not a new price list: anything bigger is a typo in the amount.
ADJUSTMENT_MAX = 1_000_00


def _staff_text(value: str) -> str:
    normalized = normalize_staff_text(value)
    if not normalized:
        raise ValueError("must not be empty")
    if len(normalized) > STAFF_REASON_MAX_LENGTH:
        raise ValueError(f"at most {STAFF_REASON_MAX_LENGTH} characters")
    return normalized


# The hard limit is checked after the spaces are squeezed; this one stops a huge string.
StaffText = Annotated[
    str, Field(max_length=2 * STAFF_REASON_MAX_LENGTH), AfterValidator(_staff_text)
]


class StaffRentalQuery(HistoryQuery):
    status: list[RentalStatus] = Field(default_factory=list, description="Any of these.")
    user_id: UUID | None = Field(default=None, description="Only this client's rentals.")
    car_id: UUID | None = None
    # Aware only: a time without an offset would silently mean different moments.
    reserved_from: AwareDatetime | None = Field(default=None, description="Inclusive.")
    reserved_to: AwareDatetime | None = Field(default=None, description="Exclusive.")

    @model_validator(mode="after")
    def _check_period(self) -> "StaffRentalQuery":
        # An empty period is a mistake in the query, not a period without rentals.
        if (
            self.reserved_from is not None
            and self.reserved_to is not None
            and self.reserved_from >= self.reserved_to
        ):
            raise ValueError("reserved_from must be earlier than reserved_to")
        return self

    def filters(self) -> RentalFilter:
        return RentalFilter(
            statuses=frozenset(self.status),
            user_id=self.user_id,
            car_id=self.car_id,
            reserved_from=self.reserved_from,
            reserved_to=self.reserved_to,
        )


class ClientRefOut(BaseModel):
    id: UUID
    first_name: str | None
    last_name: str | None

    @classmethod
    def of(cls, user_id: UUID, identity: ClientIdentity | None) -> "ClientRefOut":
        return cls(
            id=user_id,
            first_name=identity.first_name if identity else None,
            last_name=identity.last_name if identity else None,
        )


class StaffRentalItemOut(HistoryItemOut):
    client: ClientRefOut


class StaffRentalPageOut(BaseModel):
    items: list[StaffRentalItemOut] = Field(description="Newest reservation first.")
    next_cursor: str | None = Field(description="Absent on the last page.")

    @classmethod
    def of(cls, found: StaffRentalPage) -> "StaffRentalPageOut":
        items = []
        for entry in found.page.entries:
            base = HistoryItemOut.of(entry).model_dump()
            user_id = entry.rental.user_id
            client = ClientRefOut.of(user_id, found.clients.get(user_id))
            items.append(StaffRentalItemOut(**base, client=client))
        cursor = found.page.next_cursor
        return cls(items=items, next_cursor=cursor.encode() if cursor else None)


class IntervalOut(BaseModel):
    kind: IntervalKind
    started_at: datetime
    ended_at: datetime | None = Field(description="Null for the current one.")


class OperationOut(BaseModel):
    """A payment of the rental; the card itself is not shown."""

    id: UUID
    kind: OperationKind
    amount: int
    status: OperationStatus
    parent_id: UUID | None
    decline_reason: DeclineReason | None
    created_at: datetime

    @classmethod
    def of(cls, operation: PaymentOperation) -> "OperationOut":
        return cls(
            id=operation.id,
            kind=operation.kind,
            amount=operation.amount,
            status=operation.status,
            parent_id=operation.parent_id,
            decline_reason=operation.decline_reason,
            created_at=operation.created_at,
        )


class StaffAdjustmentOut(AdjustmentOut):
    comment: str
    staff_id: UUID
    staff_name: str | None


class ForcedFinishOut(BaseModel):
    reason: str
    staff_id: UUID
    staff_name: str | None


class StaffRentalOut(BaseModel):
    rental: RentalOut
    client: ClientRefOut
    intervals: list[IntervalOut]
    operations: list[OperationOut] = Field(description="Oldest first.")
    inspections: list[InspectionOut]
    adjustments: list[StaffAdjustmentOut]
    owed: int = Field(
        description="Kopecks still owed; negative: paid above the total, a refund is due."
    )
    forced_finish: ForcedFinishOut | None

    @classmethod
    def of(cls, card: StaffRentalCard, now: datetime) -> "StaffRentalOut":
        rental, names = card.view.rental, card.staff_names
        forced = None
        if rental.force_finish_reason is not None and rental.force_finished_by is not None:
            forced = ForcedFinishOut(
                reason=rental.force_finish_reason,
                staff_id=rental.force_finished_by,
                staff_name=names.get(rental.force_finished_by),
            )
        return cls(
            rental=RentalOut.of(card.view, now),
            client=ClientRefOut.of(rental.user_id, card.client),
            intervals=[
                IntervalOut(kind=i.kind, started_at=i.start, ended_at=i.end) for i in card.intervals
            ],
            operations=[OperationOut.of(operation) for operation in card.operations],
            inspections=[InspectionOut.of(inspection) for inspection in card.inspections],
            adjustments=[
                StaffAdjustmentOut(
                    id=a.id,
                    amount=a.amount,
                    created_at=a.created_at,
                    comment=a.comment,
                    staff_id=a.staff_id,
                    staff_name=names.get(a.staff_id),
                )
                for a in card.view.adjustments
            ],
            owed=card.owed,
            forced_finish=forced,
        )


class ForceFinishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: StaffText = Field(examples=["The car lost its connection, the client left it"])


class ForceFinishResultOut(BaseModel):
    rental: StaffRentalOut
    locked: bool = Field(
        description="False: the car did not confirm the lock; someone has to close it."
    )


def _real_change(amount: int) -> int:
    if amount == 0:
        raise ValueError("an adjustment changes the total")
    return amount


class AdjustmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[
        int, Field(ge=-ADJUSTMENT_MAX, le=ADJUSTMENT_MAX), AfterValidator(_real_change)
    ] = Field(description="Kopecks of BYN; negative refunds or lowers the debt.", examples=[-500])
    comment: StaffText = Field(examples=["The client waited for the car to unlock"])
