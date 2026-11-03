from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from flagman.pricing.domain import (
    DEFAULT_FREE_WAITING_MINUTES,
    MAX_FINE_AMOUNT,
    MAX_FINE_NAME_LENGTH,
    MAX_FREE_WAITING_MINUTES,
    MAX_HOLD_AMOUNT,
    MAX_PRICE_PER_MINUTE,
    FineKind,
    FineTerms,
    FineTypeVersion,
    ModelTariff,
    TariffPrices,
    TariffVersion,
)


class TariffPricesIn(BaseModel):
    """All amounts in kopecks of BYN."""

    model_config = ConfigDict(extra="forbid")

    driving_per_minute: int = Field(ge=0, le=MAX_PRICE_PER_MINUTE, strict=True)
    parked_per_minute: int = Field(ge=0, le=MAX_PRICE_PER_MINUTE, strict=True)
    waiting_per_minute: int = Field(
        ge=0,
        le=MAX_PRICE_PER_MINUTE,
        strict=True,
        description="A minute of a reservation after the free ones.",
    )
    free_waiting_minutes: int = Field(
        default=DEFAULT_FREE_WAITING_MINUTES, ge=0, le=MAX_FREE_WAITING_MINUTES, strict=True
    )
    hold_amount: int = Field(
        ge=1, le=MAX_HOLD_AMOUNT, strict=True, description="Held on the card when a trip starts."
    )

    def prices(self) -> TariffPrices:
        return TariffPrices(
            driving_per_minute=self.driving_per_minute,
            parked_per_minute=self.parked_per_minute,
            waiting_per_minute=self.waiting_per_minute,
            free_waiting_minutes=self.free_waiting_minutes,
            hold_amount=self.hold_amount,
        )


class TariffCreate(TariffPricesIn):
    model_id: UUID


class TariffUpdate(TariffPricesIn):
    based_on_version: int = Field(
        ge=1,
        strict=True,
        description=(
            "The version number the form was filled from: if someone saved a newer one meanwhile, "
            "the change is refused with `tariff-changed` instead of overwriting theirs."
        ),
    )


class TariffPricesOut(BaseModel):
    driving_per_minute: int
    parked_per_minute: int
    waiting_per_minute: int
    free_waiting_minutes: int
    hold_amount: int

    @classmethod
    def of(cls, prices: TariffPrices) -> "TariffPricesOut":
        return cls(
            driving_per_minute=prices.driving_per_minute,
            parked_per_minute=prices.parked_per_minute,
            waiting_per_minute=prices.waiting_per_minute,
            free_waiting_minutes=prices.free_waiting_minutes,
            hold_amount=prices.hold_amount,
        )


class TariffVersionOut(BaseModel):
    id: UUID
    number: int
    prices: TariffPricesOut
    valid_from: datetime = Field(description="Rentals booked from this moment use the version.")
    created_by: UUID | None = Field(description="The staff member; none for the demo seed.")

    @classmethod
    def of(cls, version: TariffVersion) -> "TariffVersionOut":
        return cls(
            id=version.id,
            number=version.number,
            prices=TariffPricesOut.of(version.prices),
            valid_from=version.valid_from,
            created_by=version.created_by,
        )


class CarModelOut(BaseModel):
    id: UUID
    brand: str
    name: str


class ModelTariffOut(BaseModel):
    model: CarModelOut
    tariff_id: UUID | None = Field(description="None until a tariff is created for the model.")
    current: TariffVersionOut | None

    @classmethod
    def of(cls, item: ModelTariff) -> "ModelTariffOut":
        model = item.model
        return cls(
            model=CarModelOut(id=model.id, brand=model.brand, name=model.name),
            tariff_id=item.current.tariff_id if item.current else None,
            current=TariffVersionOut.of(item.current) if item.current else None,
        )


class ModelTariffList(BaseModel):
    items: list[ModelTariffOut]


class TariffVersionList(BaseModel):
    items: list[TariffVersionOut]


class FineTermsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name_ru: str = Field(min_length=1, max_length=MAX_FINE_NAME_LENGTH)
    name_en: str = Field(min_length=1, max_length=MAX_FINE_NAME_LENGTH)
    amount: int = Field(ge=1, le=MAX_FINE_AMOUNT, strict=True, description="Kopecks of BYN.")

    def terms(self) -> FineTerms:
        return FineTerms(name_ru=self.name_ru, name_en=self.name_en, amount=self.amount)


class FineTypeCreate(FineTermsIn):
    code: str = Field(
        pattern=r"^[a-z][a-z0-9_]{1,39}$",
        description="Stable code of the type; a new type is always a manual fine.",
    )


class FineTypeUpdate(FineTermsIn):
    based_on_version: int = Field(
        ge=1,
        strict=True,
        description=(
            "The version number the form was filled from, as with tariffs: a newer one saved "
            "meanwhile refuses the change with `fine-type-changed`."
        ),
    )


class FineTypeVersionOut(BaseModel):
    id: UUID = Field(description="The version id.")
    fine_type_id: UUID
    code: str
    kind: FineKind = Field(description="`automatic` fines are charged by the system on its own.")
    number: int
    name_ru: str
    name_en: str
    amount: int = Field(description="Kopecks of BYN.")
    valid_from: datetime = Field(description="Fines charged from this moment use the version.")
    created_by: UUID | None = Field(description="The staff member; none for the demo seed.")

    @classmethod
    def of(cls, version: FineTypeVersion) -> "FineTypeVersionOut":
        return cls(
            id=version.id,
            fine_type_id=version.fine_type_id,
            code=version.code,
            kind=version.kind,
            number=version.number,
            name_ru=version.terms.name_ru,
            name_en=version.terms.name_en,
            amount=version.terms.amount,
            valid_from=version.valid_from,
            created_by=version.created_by,
        )


class FineTypeList(BaseModel):
    items: list[FineTypeVersionOut]
