"""Fachlogik des Lizenz-Service.

Bloomberg rechnet pro eindeutigem Instrument, Datenkategorie und Monat ab. Daraus folgen
die Regeln dieses Moduls:

* Eine Kategorie, die für ein Instrument im laufenden Monat schon abgerechnet ist,
  kostet bei einer weiteren Anfrage nichts (gold.billing_instrument_month).
* Instrumente, die mit denselben Feldlisten schon im Universum sind, werden nicht
  erneut bestellt (B3).
* Abbestellungen wirken zum Monatsende; vorher spart ein Abbruch nichts.
* Ab ``approval_threshold`` Mehrkosten, oder wenn für eine Kategorie noch kein Preis
  hinterlegt ist, braucht die Bestellung eine Freigabe durch eine zweite Person.
"""

import calendar
import json
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Connection, Engine, and_, delete, func, insert, or_, select, update

from . import db
from .bloomberg import BloombergClient, UniverseChange, bbg_identifier
from .config import Settings
from .models import (
    AuditEntry,
    CategoryCost,
    CostEstimate,
    FieldInfo,
    FieldSet,
    Identifier,
    Instrument,
    InstrumentPage,
    InstrumentStatus,
    Order,
    OrderPage,
    OrderRequest,
    OrderStatus,
    OrderType,
    PricingCategory,
    TimeSeries,
    TimeSeriesPoint,
    UsageEntry,
)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


IDENTIFIER_COLUMNS = {"ISIN": "isin", "FIGI": "figi", "CUSIP": "cusip", "SEDOL": "sedol", "TICKER": "ticker"}
SUBSCRIBED = (InstrumentStatus.ACTIVE, InstrumentStatus.PENDING_SUBSCRIBE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


class LicenseService:
    def __init__(
        self,
        engine: Engine,
        bloomberg: BloombergClient,
        settings: Settings,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.engine = engine
        self.bloomberg = bloomberg
        self.settings = settings
        self.clock = clock

    # ------------------------------------------------------------------ Hilfen

    def _month(self) -> str:
        return self.clock().strftime("%Y-%m")

    def _audit(self, conn: Connection, user: str, action: str, entity: str, entity_id: str, details=None):
        conn.execute(insert(db.audit_log).values(
            at=self.clock(), user_name=user, action=action, entity=entity, entity_id=entity_id,
            details=json.dumps(details, default=str) if details is not None else None,
        ))

    @staticmethod
    def _resolve(conn: Connection, ident: Identifier):
        column = db.instrument.c[IDENTIFIER_COLUMNS[ident.type]]
        return conn.execute(select(db.instrument).where(column == ident.value)).mappings().first()

    @staticmethod
    def _field_set_categories(conn: Connection, field_sets: list[str]) -> set[str]:
        known = set(conn.execute(
            select(db.field_set.c.field_set_id).where(db.field_set.c.field_set_id.in_(field_sets))
        ).scalars())
        missing = sorted(set(field_sets) - known)
        if missing:
            raise Invalid(f"Unbekannte Feldlisten: {', '.join(missing)}")
        return set(conn.execute(
            select(db.field_catalog.c.category).distinct()
            .join(db.field_set_field, db.field_set_field.c.mnemonic == db.field_catalog.c.mnemonic)
            .where(db.field_set_field.c.field_set_id.in_(field_sets))
        ).scalars())

    @staticmethod
    def _fields(conn: Connection, field_sets: list[str]) -> list[str]:
        return sorted(set(conn.execute(
            select(db.field_set_field.c.mnemonic).where(db.field_set_field.c.field_set_id.in_(field_sets))
        ).scalars()))

    @staticmethod
    def _price(conn: Connection, category: str, on: date) -> tuple[Decimal, str] | None:
        p = db.pricing_category.c
        row = conn.execute(
            select(p.price_per_instrument, p.currency)
            .where(p.category == category, p.valid_from <= on, or_(p.valid_to.is_(None), p.valid_to > on))
            .order_by(p.valid_from.desc())
        ).first()
        return (Decimal(row[0]), row[1]) if row else None

    def _billed(self, conn: Connection, instrument_id: str) -> set[str]:
        b = db.billing_instrument_month.c
        return set(conn.execute(
            select(b.category).where(b.billing_month == self._month(), b.instrument_id == instrument_id)
        ).scalars())

    def _instrument_field_sets(self, conn: Connection, instrument_id: str) -> set[str]:
        f = db.instrument_field_set.c
        return set(conn.execute(select(f.field_set_id).where(f.instrument_id == instrument_id)).scalars())

    # ------------------------------------------------------------- Prognose (B2, B3)

    def _plan(self, conn: Connection, req: OrderRequest) -> tuple[CostEstimate, list[dict]]:
        """Berechnet die Kostenprognose und je Identifier, ob er ein Duplikat ist."""
        items: list[dict] = []
        duplicates: list[Identifier] = []
        seen: set[tuple[str, str]] = set()
        new_per_category: Counter[str] = Counter()
        already_billed = 0

        if req.type == OrderType.SUBSCRIBE:
            if not req.field_sets:
                raise Invalid("Eine Bestellung braucht mindestens eine Feldliste")
            needed = self._field_set_categories(conn, req.field_sets)

        for ident in req.instruments:
            key = (ident.type, ident.value)
            inst = self._resolve(conn, ident)
            if req.type == OrderType.SUBSCRIBE:
                is_dup = key in seen or (
                    inst is not None
                    and inst["status"] in SUBSCRIBED
                    and set(req.field_sets) <= self._instrument_field_sets(conn, inst["instrument_id"])
                )
                if not is_dup:
                    new_categories = needed - (self._billed(conn, inst["instrument_id"]) if inst else set())
                    if new_categories:
                        new_per_category.update(new_categories)
                    else:
                        already_billed += 1
            else:
                if inst is None:
                    raise Invalid(f"{ident.type} {ident.value} ist nicht im Universum")
                is_dup = key in seen or inst["status"] not in SUBSCRIBED
            seen.add(key)
            if is_dup:
                duplicates.append(ident)
            items.append({"ident": ident, "instrument_id": inst["instrument_id"] if inst else None,
                          "is_duplicate": is_dup})

        today = self.clock().date()
        by_category, unpriced, total = [], [], Decimal("0")
        for category, count in sorted(new_per_category.items()):
            price = self._price(conn, category, today)
            if price is None:
                unpriced.append(category)
                cost = Decimal("0")
            else:
                cost = price[0] * count
            total += cost
            by_category.append(CategoryCost(category=category, new_unique_instruments=count, cost=cost))

        estimate = CostEstimate(
            currency=self.settings.currency,
            additional_monthly_cost=total,
            already_billed_instruments=already_billed,
            duplicates=duplicates,
            unpriced_categories=unpriced,
            by_category=by_category,
        )
        return estimate, items

    def estimate(self, req: OrderRequest) -> CostEstimate:
        with self.engine.connect() as conn:
            return self._plan(conn, req)[0]

    # -------------------------------------------------------------- Bestellungen

    def create_order(self, req: OrderRequest, user: str, idempotency_key: str | None = None) -> Order:
        with self.engine.begin() as conn:
            if idempotency_key:
                o = db.order.c
                existing = conn.execute(select(o.order_id).where(
                    o.idempotency_key == idempotency_key, o.requested_by == user)).scalar()
                if existing:
                    return self._load_order(conn, existing)

            estimate, items = self._plan(conn, req)
            if all(i["is_duplicate"] for i in items):
                raise Invalid("Keine Änderung: alle Instrumente sind bereits im gewünschten Zustand")

            requires_approval = req.type == OrderType.SUBSCRIBE and (
                estimate.additional_monthly_cost >= self.settings.approval_threshold
                or bool(estimate.unpriced_categories)
            )
            order_id = str(uuid.uuid4())
            conn.execute(insert(db.order).values(
                order_id=order_id,
                order_type=req.type,
                status=OrderStatus.PENDING_APPROVAL if requires_approval else OrderStatus.APPROVED,
                reason=req.reason,
                field_sets=",".join(req.field_sets),
                estimated_cost_chf=estimate.additional_monthly_cost,
                estimate_json=estimate.model_dump_json(by_alias=True),
                requires_approval=requires_approval,
                requested_by=user,
                requested_at=self.clock(),
                idempotency_key=idempotency_key,
            ))
            conn.execute(insert(db.order_item), [
                {"order_id": order_id, "line_no": n, "identifier_type": i["ident"].type,
                 "identifier_value": i["ident"].value, "instrument_id": i["instrument_id"],
                 "is_duplicate": i["is_duplicate"]}
                for n, i in enumerate(items, start=1)
            ])
            self._audit(conn, user, "ORDER_CREATED", "order", order_id,
                        {"type": req.type, "cost": estimate.additional_monthly_cost,
                         "requiresApproval": requires_approval})
            if not requires_approval:
                self._execute(conn, order_id, user)
            return self._load_order(conn, order_id)

    def approve(self, order_id: str, user: str, comment: str | None) -> Order:
        return self._decide(order_id, user, comment, approve=True)

    def reject(self, order_id: str, user: str, comment: str | None) -> Order:
        if not comment:
            raise Invalid("Eine Ablehnung braucht eine Begründung")
        return self._decide(order_id, user, comment, approve=False)

    def _decide(self, order_id: str, user: str, comment: str | None, approve: bool) -> Order:
        with self.engine.begin() as conn:
            row = conn.execute(select(db.order).where(db.order.c.order_id == order_id)).mappings().first()
            if row is None:
                raise NotFound(order_id)
            if row["status"] != OrderStatus.PENDING_APPROVAL:
                raise Conflict(f"Bestellung hat Status {row['status']}")
            if row["requested_by"] == user:
                raise Forbidden("Die Freigabe muss durch eine zweite Person erfolgen")
            conn.execute(update(db.order).where(db.order.c.order_id == order_id).values(
                status=OrderStatus.APPROVED if approve else OrderStatus.REJECTED,
                decided_by=user, decided_at=self.clock(), decision_comment=comment,
            ))
            self._audit(conn, user, "ORDER_APPROVED" if approve else "ORDER_REJECTED", "order", order_id,
                        {"comment": comment})
            if approve:
                self._execute(conn, order_id, user)
            return self._load_order(conn, order_id)

    def _execute(self, conn: Connection, order_id: str, user: str) -> None:
        row = conn.execute(select(db.order).where(db.order.c.order_id == order_id)).mappings().one()
        items = conn.execute(select(db.order_item).where(
            db.order_item.c.order_id == order_id, db.order_item.c.is_duplicate.is_(False))).mappings().all()
        now, today = self.clock(), self.clock().date()

        if row["order_type"] == OrderType.UNSUBSCRIBE:
            # Bloomberg rechnet pro Monat ab: Entfernt wird erst zum Monatsende (apply_due_unsubscriptions).
            effective = month_end(today)
            ids = [i["instrument_id"] for i in items]
            conn.execute(update(db.instrument).where(db.instrument.c.instrument_id.in_(ids)).values(
                status=InstrumentStatus.PENDING_UNSUBSCRIBE, unsubscribe_effective=effective, updated_at=now))
            conn.execute(update(db.order).where(db.order.c.order_id == order_id).values(
                status=OrderStatus.SUBMITTED, effective_date=effective))
            self._audit(conn, user, "UNSUBSCRIBE_SCHEDULED", "order", order_id, {"effective": effective})
            return

        field_sets = row["field_sets"].split(",")
        categories = self._field_set_categories(conn, field_sets)
        added = []
        for item in items:
            instrument_id = item["instrument_id"]
            if instrument_id is None:
                instrument_id = str(uuid.uuid4())
                conn.execute(insert(db.instrument).values(
                    instrument_id=instrument_id, status=InstrumentStatus.ACTIVE, source="MANUAL",
                    subscribed_since=today, created_at=now, updated_at=now,
                    **{IDENTIFIER_COLUMNS[item["identifier_type"]]: item["identifier_value"]},
                ))
                conn.execute(update(db.order_item).where(
                    db.order_item.c.order_id == order_id, db.order_item.c.line_no == item["line_no"]
                ).values(instrument_id=instrument_id))
            else:
                # Eine erneute Bestellung hebt eine vorgemerkte Abbestellung auf.
                conn.execute(update(db.instrument).where(db.instrument.c.instrument_id == instrument_id).values(
                    status=InstrumentStatus.ACTIVE, unsubscribe_effective=None, updated_at=now,
                    subscribed_since=func.coalesce(db.instrument.c.subscribed_since, today)))
            for fs in set(field_sets) - self._instrument_field_sets(conn, instrument_id):
                conn.execute(insert(db.instrument_field_set).values(
                    instrument_id=instrument_id, field_set_id=fs, since=today))
            for category in categories - self._billed(conn, instrument_id):
                conn.execute(insert(db.billing_instrument_month).values(
                    billing_month=self._month(), instrument_id=instrument_id, category=category,
                    first_requested_at=now))
            added.append(bbg_identifier(item["identifier_type"], item["identifier_value"]))

        request_id = self.bloomberg.submit(
            UniverseChange(add=added, remove=[], fields=self._fields(conn, field_sets)))
        conn.execute(update(db.order).where(db.order.c.order_id == order_id).values(
            status=OrderStatus.SUBMITTED, bbg_request_id=request_id, effective_date=today))
        self._audit(conn, user, "ORDER_SUBMITTED", "order", order_id, {"bbgRequestId": request_id})

    def apply_due_unsubscriptions(self, user: str = "system") -> list[str]:
        """Entfernt fällige Abbestellungen bei Bloomberg; täglich nach Monatsende aufrufen."""
        today = self.clock().date()
        with self.engine.begin() as conn:
            i = db.instrument.c
            due = conn.execute(select(db.instrument).where(
                i.status == InstrumentStatus.PENDING_UNSUBSCRIBE, i.unsubscribe_effective < today
            )).mappings().all()
            if not due:
                return []
            removed = []
            for inst in due:
                id_type = next(t for t, col in IDENTIFIER_COLUMNS.items() if inst[col])
                removed.append(bbg_identifier(id_type, inst[IDENTIFIER_COLUMNS[id_type]]))
            request_id = self.bloomberg.submit(UniverseChange(add=[], remove=removed, fields=[]))
            ids = [inst["instrument_id"] for inst in due]
            conn.execute(update(db.instrument).where(i.instrument_id.in_(ids)).values(
                status=InstrumentStatus.INACTIVE, updated_at=self.clock()))
            conn.execute(delete(db.instrument_field_set).where(db.instrument_field_set.c.instrument_id.in_(ids)))
            self._audit(conn, user, "UNSUBSCRIBE_APPLIED", "instrument", ",".join(ids), {"bbgRequestId": request_id})
            return ids

    def _load_order(self, conn: Connection, order_id: str) -> Order:
        row = conn.execute(select(db.order).where(db.order.c.order_id == order_id)).mappings().first()
        if row is None:
            raise NotFound(order_id)
        items = conn.execute(select(db.order_item).where(db.order_item.c.order_id == order_id)
                             .order_by(db.order_item.c.line_no)).mappings().all()
        return Order(
            id=row["order_id"],
            type=row["order_type"],
            instruments=[Identifier(type=i["identifier_type"], value=i["identifier_value"]) for i in items],
            field_sets=[f for f in (row["field_sets"] or "").split(",") if f],
            reason=row["reason"],
            status=row["status"],
            estimate=CostEstimate.model_validate_json(row["estimate_json"]) if row["estimate_json"] else None,
            requires_approval=row["requires_approval"],
            requested_by=row["requested_by"],
            requested_at=row["requested_at"],
            approved_by=row["decided_by"] if row["status"] != OrderStatus.REJECTED else None,
            approved_at=row["decided_at"] if row["status"] != OrderStatus.REJECTED else None,
            decision_comment=row["decision_comment"],
            effective_date=row["effective_date"],
        )

    def get_order(self, order_id: str) -> Order:
        with self.engine.connect() as conn:
            return self._load_order(conn, order_id)

    def list_orders(self, status: str | None, requested_by: str | None, page: int, page_size: int) -> OrderPage:
        o = db.order.c
        cond = and_(
            o.status == status if status else True,
            o.requested_by == requested_by if requested_by else True,
        )
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(db.order).where(cond)).scalar_one()
            ids = conn.execute(select(o.order_id).where(cond).order_by(o.requested_at.desc())
                               .offset((page - 1) * page_size).limit(page_size)).scalars().all()
            return OrderPage(items=[self._load_order(conn, i) for i in ids], total=total)

    # ------------------------------------------------------------- Instrumente

    def _to_instrument(self, conn: Connection, row) -> Instrument:
        field_sets = sorted(self._instrument_field_sets(conn, row["instrument_id"]))
        categories = sorted(self._field_set_categories(conn, field_sets)) if field_sets else []
        return Instrument(
            id=row["instrument_id"],
            identifiers=[Identifier(type=t, value=row[col]) for t, col in IDENTIFIER_COLUMNS.items() if row[col]],
            name=row["name"],
            status=row["status"],
            field_sets=field_sets,
            categories=categories,
            source=row["source"],
            subscribed_since=row["subscribed_since"],
            unsubscribe_effective=row["unsubscribe_effective"],
        )

    def list_instruments(self, status: str | None, search: str | None, page: int, page_size: int) -> InstrumentPage:
        i = db.instrument.c
        conds = []
        if status:
            conds.append(i.status == status)
        if search:
            like = f"%{search}%"
            conds.append(or_(*(i[c].ilike(like) for c in (*IDENTIFIER_COLUMNS.values(), "name"))))
        cond = and_(True, *conds)
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(db.instrument).where(cond)).scalar_one()
            rows = conn.execute(select(db.instrument).where(cond).order_by(i.created_at.desc())
                                .offset((page - 1) * page_size).limit(page_size)).mappings().all()
            return InstrumentPage(items=[self._to_instrument(conn, r) for r in rows], total=total)

    def get_instrument(self, instrument_id: str) -> Instrument:
        with self.engine.connect() as conn:
            row = conn.execute(select(db.instrument).where(
                db.instrument.c.instrument_id == instrument_id)).mappings().first()
            if row is None:
                raise NotFound(instrument_id)
            return self._to_instrument(conn, row)

    # ------------------------------------------------------------ Kosten (B2)

    def usage(self, month: str) -> list[UsageEntry]:
        b = db.billing_instrument_month.c
        first_day = date.fromisoformat(f"{month}-01")
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(b.category, func.count(b.instrument_id.distinct()))
                .where(b.billing_month == month).group_by(b.category).order_by(b.category)
            ).all()
            result = []
            for category, count in rows:
                price = self._price(conn, category, first_day)
                result.append(UsageEntry(
                    month=month, category=category, unique_instruments=count,
                    cost=price[0] * count if price else None, currency=price[1] if price else None))
            return result

    def get_pricing(self) -> list[PricingCategory]:
        p = db.pricing_category.c
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.pricing_category).where(p.valid_to.is_(None))
                                .order_by(p.category)).mappings().all()
            return [PricingCategory(category=r["category"], price_per_instrument=r["price_per_instrument"],
                                    currency=r["currency"], valid_from=r["valid_from"]) for r in rows]

    def put_pricing(self, prices: list[PricingCategory], user: str) -> None:
        """Setzt neue Preise; bisherige Preise bleiben mit Enddatum für die Historie erhalten."""
        p = db.pricing_category.c
        with self.engine.begin() as conn:
            for price in prices:
                conn.execute(update(db.pricing_category).where(
                    p.category == price.category, p.valid_to.is_(None), p.valid_from < price.valid_from
                ).values(valid_to=price.valid_from))
                conn.execute(delete(db.pricing_category).where(
                    p.category == price.category, p.valid_from == price.valid_from))
                conn.execute(insert(db.pricing_category).values(
                    category=price.category, valid_from=price.valid_from,
                    price_per_instrument=price.price_per_instrument, currency=price.currency))
            self._audit(conn, user, "PRICING_CHANGED", "pricing", "categories",
                        [x.model_dump(by_alias=True) for x in prices])

    # ------------------------------------------------------- Katalog, Zeitreihen, Prüfpfad

    def fields(self, search: str | None, category: str | None) -> list[FieldInfo]:
        f = db.field_catalog.c
        conds = []
        if search:
            conds.append(or_(f.mnemonic.ilike(f"%{search}%"), f.description.ilike(f"%{search}%")))
        if category:
            conds.append(f.category == category)
        today = self.clock().date()
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.field_catalog).where(and_(True, *conds))
                                .order_by(f.mnemonic)).mappings().all()
            prices = {c: self._price(conn, c, today) for c in {r["category"] for r in rows}}
            return [FieldInfo(mnemonic=r["mnemonic"], description=r["description"], category=r["category"],
                              price_per_instrument=prices[r["category"]][0] if prices[r["category"]] else None)
                    for r in rows]

    def field_sets(self) -> list[FieldSet]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.field_set).order_by(db.field_set.c.field_set_id)).mappings().all()
            return [FieldSet(id=r["field_set_id"], name=r["name"], description=r["description"],
                             fields=self._fields(conn, [r["field_set_id"]]),
                             categories=sorted(self._field_set_categories(conn, [r["field_set_id"]])))
                    for r in rows]

    def timeseries(self, instrument_id: str, field: str, start: date, end: date,
                   as_of: datetime | None) -> TimeSeries:
        t = db.timeseries_value.c
        conds = [t.instrument_id == instrument_id, t.field == field, t.value_date >= start, t.value_date <= end]
        if as_of is None:
            conds.append(t.superseded_at.is_(None))
        else:
            conds += [t.recorded_at <= as_of, or_(t.superseded_at.is_(None), t.superseded_at > as_of)]
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.timeseries_value).where(*conds).order_by(t.value_date)).mappings().all()
        return TimeSeries(
            instrument_id=instrument_id, field=field, as_of=as_of,
            points=[TimeSeriesPoint(date=r["value_date"],
                                    value=r["value_num"] if r["value_num"] is not None else r["value_str"],
                                    recorded_at=r["recorded_at"]) for r in rows],
        )

    def audit(self, instrument_id: str | None, user: str | None, start: datetime | None,
              end: datetime | None) -> list[AuditEntry]:
        a = db.audit_log.c
        conds = []
        if instrument_id:
            conds.append(a.entity_id.contains(instrument_id))
        if user:
            conds.append(a.user_name == user)
        if start:
            conds.append(a.at >= start)
        if end:
            conds.append(a.at <= end)
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.audit_log).where(and_(True, *conds))
                                .order_by(a.at.desc())).mappings().all()
        return [AuditEntry(at=r["at"], user=r["user_name"], action=r["action"], entity=r["entity"],
                           entity_id=r["entity_id"], details=json.loads(r["details"]) if r["details"] else None)
                for r in rows]
