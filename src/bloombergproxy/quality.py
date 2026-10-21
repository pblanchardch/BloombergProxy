"""Datenqualität (D1): Prüfungen nach jeder Tageslieferung.

* MISSING_VALUE – ein Feld aus den Feldlisten eines Instruments fehlt für den Liefertag;
* STALE_PRICE – PX_LAST ist an den letzten ``dq_stale_days`` gelieferten Tagen unverändert;
* OUTLIER – PX_LAST weicht um mehr als ``dq_outlier_share`` vom vorherigen Wert ab.

Jede Ausnahme wird pro Typ, Instrument, Feld und Tag nur einmal angelegt und dann
bearbeitet: OPEN -> ACKNOWLEDGED -> RESOLVED.
"""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import Connection, select, update

from . import db
from .models import ApiModel, InstrumentStatus
from .service import Invalid, LicenseService, NotFound

PRICE_FIELD = "PX_LAST"
STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")


class DqException(ApiModel):
    id: str
    type: str
    instrument_id: str
    field: str
    date: date
    detail: str | None
    status: str
    detected_at: object
    handled_by: str | None


class StatusChange(BaseModel):
    status: str
    comment: str | None = None


class DataQuality:
    def __init__(self, service: LicenseService):
        self.svc = service
        self.settings = service.settings

    def check(self, as_of: date) -> list[dict]:
        """Prüft den Liefertag und legt neue Ausnahmen an."""
        found: list[dict] = []
        i, f, t = db.instrument.c, db.instrument_field_set.c, db.timeseries_value.c
        with self.svc.engine.begin() as conn:
            instruments = conn.execute(select(i.instrument_id).where(i.status.in_(
                [InstrumentStatus.ACTIVE, InstrumentStatus.PENDING_UNSUBSCRIBE]))).scalars().all()
            for instrument_id in instruments:
                field_sets = list(conn.execute(select(f.field_set_id).where(f.instrument_id == instrument_id)).scalars())
                if not field_sets:
                    continue
                delivered = set(conn.execute(select(t.field).where(
                    t.instrument_id == instrument_id, t.value_date == as_of, t.superseded_at.is_(None))).scalars())
                for field in self.svc._fields(conn, field_sets):
                    if field not in delivered:
                        found.append(self._add(conn, "MISSING_VALUE", instrument_id, field, as_of,
                                               "Kein Wert geliefert"))
                if PRICE_FIELD in delivered:
                    found += self._check_price(conn, instrument_id, as_of)
        return [x for x in found if x]

    def _check_price(self, conn: Connection, instrument_id: str, as_of: date) -> list[dict | None]:
        t = db.timeseries_value.c
        rows = conn.execute(
            select(t.value_date, t.value_num).where(
                t.instrument_id == instrument_id, t.field == PRICE_FIELD, t.value_date <= as_of,
                t.source == self.settings.source_precedence[0],
                t.superseded_at.is_(None), t.value_num.is_not(None))
            .order_by(t.value_date.desc()).limit(max(self.settings.dq_stale_days, 2))
        ).all()
        found: list[dict | None] = []
        if len(rows) >= 2:
            current, previous = Decimal(rows[0][1]), Decimal(rows[1][1])
            if (previous and abs(current / previous - 1) > Decimal(str(self.settings.dq_outlier_share))
                    and not self._split_between(conn, instrument_id, rows[1][0], rows[0][0])):
                found.append(self._add(conn, "OUTLIER", instrument_id, PRICE_FIELD, as_of,
                                       f"{previous.normalize():f} -> {current.normalize():f} ({(current / previous - 1):+.1%})"))
        found += self._check_sources(conn, instrument_id, as_of)
        n = self.settings.dq_stale_days
        if len(rows) >= n and len({Decimal(r[1]) for r in rows[:n]}) == 1:
            found.append(self._add(conn, "STALE_PRICE", instrument_id, PRICE_FIELD, as_of,
                                   f"Seit {n} Liefertagen unverändert {Decimal(rows[0][1]).normalize():f}"))
        return found

    @staticmethod
    def _split_between(conn: Connection, instrument_id: str, after: date, until: date) -> bool:
        """Ein Split erklärt einen Preissprung (A2): dann kein OUTLIER."""
        c = db.corporate_action.c
        return conn.execute(select(c.row_id).where(
            c.instrument_id == instrument_id, c.action_type == "STOCK_SPLIT", c.status != "CANCELLED",
            c.superseded_at.is_(None), c.ex_date > after, c.ex_date <= until)).first() is not None

    def _check_sources(self, conn: Connection, instrument_id: str, as_of: date) -> list[dict | None]:
        """SOURCE_MISMATCH: Quellen weichen beim Preis um mehr als ``dq_source_tolerance`` ab."""
        t = db.timeseries_value.c
        values = dict(conn.execute(select(t.source, t.value_num).where(
            t.instrument_id == instrument_id, t.field == PRICE_FIELD, t.value_date == as_of,
            t.superseded_at.is_(None), t.value_num.is_not(None))).all())
        if len(values) < 2:
            return []
        low, high = min(map(Decimal, values.values())), max(map(Decimal, values.values()))
        if low and high / low - 1 > Decimal(str(self.settings.dq_source_tolerance)):
            detail = ", ".join(f"{s} {Decimal(v).normalize():f}" for s, v in sorted(values.items()))
            return [self._add(conn, "SOURCE_MISMATCH", instrument_id, PRICE_FIELD, as_of, detail)]
        return []

    def _add(self, conn: Connection, kind: str, instrument_id: str, field: str, day: date, detail: str) -> dict | None:
        q = db.dq_exception.c
        exists = conn.execute(select(q.exception_id).where(
            q.type == kind, q.instrument_id == instrument_id, q.field == field, q.value_date == day)).first()
        if exists:
            return None
        row = {"exception_id": str(uuid.uuid4()), "type": kind, "instrument_id": instrument_id, "field": field,
               "value_date": day, "detail": detail, "detected_at": self.svc.clock(), "status": "OPEN"}
        conn.execute(db.dq_exception.insert().values(**row))
        return row

    def list(self, kind: str | None, status: str | None) -> list[DqException]:
        q = db.dq_exception.c
        conds = [q.type == kind] if kind else []
        if status:
            conds.append(q.status == status)
        with self.svc.engine.connect() as conn:
            rows = conn.execute(select(db.dq_exception).where(*conds)
                                .order_by(q.value_date.desc(), q.type)).mappings().all()
        return [self._model(r) for r in rows]

    def set_status(self, exception_id: str, change: StatusChange, user: str) -> DqException:
        if change.status not in STATUSES:
            raise Invalid(f"Status muss einer von {', '.join(STATUSES)} sein")
        q = db.dq_exception.c
        with self.svc.engine.begin() as conn:
            if conn.execute(select(q.exception_id).where(q.exception_id == exception_id)).first() is None:
                raise NotFound(exception_id)
            conn.execute(update(db.dq_exception).where(q.exception_id == exception_id).values(
                status=change.status, handled_by=user, handled_at=self.svc.clock()))
            self.svc._audit(conn, user, f"DQ_{change.status}", "dq_exception", exception_id,
                            {"comment": change.comment})
            row = conn.execute(select(db.dq_exception).where(q.exception_id == exception_id)).mappings().one()
        return self._model(row)

    @staticmethod
    def _model(r) -> DqException:
        return DqException(id=r["exception_id"], type=r["type"], instrument_id=r["instrument_id"], field=r["field"],
                           date=r["value_date"], detail=r["detail"], status=r["status"],
                           detected_at=r["detected_at"], handled_by=r["handled_by"])
