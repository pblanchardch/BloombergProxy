"""REST-Endpunkte gemäss api/openapi.yaml."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from .models import (
    AuditEntry,
    CostEstimate,
    Decision,
    FieldInfo,
    FieldSet,
    Instrument,
    InstrumentPage,
    InstrumentStatus,
    Order,
    OrderPage,
    OrderRequest,
    OrderStatus,
    PricingCategory,
    TimeSeries,
    UsageEntry,
)
from .service import LicenseService

router = APIRouter(prefix="/api/v1")


@dataclass(frozen=True)
class User:
    name: str
    scopes: frozenset[str]


def current_user(
    x_user: Annotated[str | None, Header()] = None,
    x_scopes: Annotated[str | None, Header()] = None,
) -> User:
    """Entwicklungsmodus: Benutzer und Scopes aus Headern. Im Betrieb durch OAuth2 ersetzen."""
    if not x_user:
        raise HTTPException(401, "X-User fehlt")
    scopes = {s.strip() for s in (x_scopes or "lizenz.read").split(",") if s.strip()}
    return User(x_user, frozenset(scopes))


def require(scope: str):
    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if scope not in user.scopes:
            raise HTTPException(403, f"Scope {scope} fehlt")
        return user
    return check


def service(request: Request) -> LicenseService:
    return request.app.state.service


Svc = Annotated[LicenseService, Depends(service)]
Reader = Annotated[User, Depends(require("lizenz.read"))]
Orderer = Annotated[User, Depends(require("lizenz.order"))]
Approver = Annotated[User, Depends(require("lizenz.approve"))]
Admin = Annotated[User, Depends(require("lizenz.admin"))]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(alias="pageSize", ge=1, le=500)]


@router.get("/instruments", response_model=InstrumentPage, response_model_by_alias=True)
def list_instruments(svc: Svc, _: Reader, status: InstrumentStatus | None = None, search: str | None = None,
                     page: Page = 1, page_size: PageSize = 50):
    return svc.list_instruments(status, search, page, page_size)


@router.get("/instruments/{instrument_id}", response_model=Instrument)
def get_instrument(instrument_id: str, svc: Svc, _: Reader):
    return svc.get_instrument(instrument_id)


@router.post("/orders", response_model=Order, status_code=201)
def create_order(req: OrderRequest, svc: Svc, user: Orderer,
                 idempotency_key: Annotated[str | None, Header()] = None):
    return svc.create_order(req, user.name, idempotency_key)


@router.get("/orders", response_model=OrderPage)
def list_orders(svc: Svc, _: Reader, status: OrderStatus | None = None,
                requested_by: Annotated[str | None, Query(alias="requestedBy")] = None,
                page: Page = 1, page_size: PageSize = 50):
    return svc.list_orders(status, requested_by, page, page_size)


@router.post("/orders/estimate", response_model=CostEstimate)
def estimate(req: OrderRequest, svc: Svc, _: Reader):
    return svc.estimate(req)


@router.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str, svc: Svc, _: Reader):
    return svc.get_order(order_id)


@router.post("/orders/{order_id}/approve", response_model=Order)
def approve(order_id: str, svc: Svc, user: Approver, decision: Decision | None = None):
    return svc.approve(order_id, user.name, decision.comment if decision else None)


@router.post("/orders/{order_id}/reject", response_model=Order)
def reject(order_id: str, decision: Decision, svc: Svc, user: Approver):
    return svc.reject(order_id, user.name, decision.comment)


@router.get("/usage", response_model=list[UsageEntry])
def usage(svc: Svc, _: Reader, month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]):
    return svc.usage(month)


@router.get("/pricing/categories", response_model=list[PricingCategory])
def get_pricing(svc: Svc, _: Reader):
    return svc.get_pricing()


@router.put("/pricing/categories", status_code=200)
def put_pricing(prices: list[PricingCategory], svc: Svc, user: Admin):
    svc.put_pricing(prices, user.name)
    return {"status": "ok"}


@router.get("/timeseries", response_model=TimeSeries)
def timeseries(svc: Svc, _: Reader,
               instrument_id: Annotated[str, Query(alias="instrumentId")],
               field: str,
               start: Annotated[date, Query(alias="from")],
               end: Annotated[date, Query(alias="to")],
               as_of: Annotated[datetime | None, Query(alias="asOf")] = None,
               format: str = "json"):
    ts = svc.timeseries(instrument_id, field, start, end, as_of)
    if format != "csv":
        return ts
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "value", "recordedAt"])
    for p in ts.points:
        writer.writerow([p.date.isoformat(), p.value, p.recorded_at.isoformat()])
    return Response(out.getvalue(), media_type="text/csv")


@router.get("/fields", response_model=list[FieldInfo])
def fields(svc: Svc, _: Reader, search: str | None = None, category: str | None = None):
    return svc.fields(search, category)


@router.get("/field-sets", response_model=list[FieldSet])
def field_sets(svc: Svc, _: Reader):
    return svc.field_sets()


@router.get("/audit", response_model=list[AuditEntry])
def audit(svc: Svc, _: Reader,
          instrument_id: Annotated[str | None, Query(alias="instrumentId")] = None,
          user: str | None = None,
          start: Annotated[datetime | None, Query(alias="from")] = None,
          end: Annotated[datetime | None, Query(alias="to")] = None):
    return svc.audit(instrument_id, user, start, end)
