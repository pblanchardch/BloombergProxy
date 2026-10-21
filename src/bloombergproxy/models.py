"""Pydantic-Modelle gemäss api/openapi.yaml (JSON in camelCase)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class IdentifierType(StrEnum):
    ISIN = "ISIN"
    FIGI = "FIGI"
    CUSIP = "CUSIP"
    SEDOL = "SEDOL"
    TICKER = "TICKER"


class InstrumentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PENDING_SUBSCRIBE = "PENDING_SUBSCRIBE"
    PENDING_UNSUBSCRIBE = "PENDING_UNSUBSCRIBE"
    INACTIVE = "INACTIVE"


class OrderType(StrEnum):
    SUBSCRIBE = "SUBSCRIBE"
    UNSUBSCRIBE = "UNSUBSCRIBE"


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Identifier(ApiModel):
    type: IdentifierType
    value: str = Field(min_length=1)


class Instrument(ApiModel):
    id: str
    identifiers: list[Identifier]
    name: str | None = None
    status: InstrumentStatus
    field_sets: list[str] = []
    categories: list[str] = []
    source: str
    subscribed_since: date | None = None
    unsubscribe_effective: date | None = None


class InstrumentPage(ApiModel):
    items: list[Instrument]
    total: int


class OrderRequest(ApiModel):
    type: OrderType
    instruments: list[Identifier] = Field(min_length=1)
    field_sets: list[str] = []
    reason: str | None = None


class CategoryCost(ApiModel):
    category: str
    new_unique_instruments: int
    cost: Decimal


class CostEstimate(ApiModel):
    currency: str
    additional_monthly_cost: Decimal
    already_billed_instruments: int
    duplicates: list[Identifier] = []
    unpriced_categories: list[str] = []
    by_category: list[CategoryCost] = []


class Order(OrderRequest):
    id: str
    status: OrderStatus
    estimate: CostEstimate | None = None
    requires_approval: bool
    requested_by: str
    requested_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    decision_comment: str | None = None
    effective_date: date | None = None
    source: str = "MANUAL"


class OrderPage(ApiModel):
    items: list[Order]
    total: int


class Decision(ApiModel):
    comment: str | None = None


class UsageEntry(ApiModel):
    month: str
    category: str
    unique_instruments: int
    cost: Decimal | None
    currency: str | None


class PricingCategory(ApiModel):
    category: str
    price_per_instrument: Decimal = Field(ge=0)
    currency: str
    valid_from: date


class TimeSeriesPoint(ApiModel):
    date: date
    value: Decimal | str | None
    recorded_at: datetime


class TimeSeries(ApiModel):
    instrument_id: str
    field: str
    as_of: datetime | None
    points: list[TimeSeriesPoint]


class FieldInfo(ApiModel):
    mnemonic: str
    description: str | None
    category: str
    price_per_instrument: Decimal | None


class FieldSet(ApiModel):
    id: str
    name: str
    description: str | None
    fields: list[str]
    categories: list[str]


class AuditEntry(ApiModel):
    at: datetime
    user: str
    action: str
    entity: str
    entity_id: str
    details: dict | None
