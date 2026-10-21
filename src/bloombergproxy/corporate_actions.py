"""Corporate Actions (A2).

Ereignisse kommen von Bloomberg (Tageslauf) und anderen Anbietern (CSV-Import). Jede Quelle
meldet ein Ereignis unter ihrer eigenen Kennung; Änderungen werden bitemporal gespeichert.

Golden Copy: Meldungen verschiedener Quellen zum selben Instrument, derselben Art und demselben
Ex-Datum bilden ein Ereignis. Massgebend ist die Quelle mit dem höchsten Rang
(``source_precedence``). Weichen Betrag, Verhältnis, Zahltag oder Status voneinander ab,
entsteht die Datenqualitäts-Ausnahme CA_MISMATCH.
"""

import csv
import io
import json
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Connection, insert, select, update

from . import db, reference
from .bloomberg import CorporateAction
from .models import ApiModel, InstrumentStatus
from .reconcile import _parse_date
from .service import Invalid, LicenseService
from .vendors import check_vendor, parse_value

TYPES = ("DVD_CASH", "STOCK_SPLIT", "RIGHTS_ISSUE", "MERGER", "NAME_CHANGE", "OTHER")
STATUSES = ("ANNOUNCED", "CONFIRMED", "CANCELLED")
COMPARED = ("status", "pay_date", "amount", "currency", "ratio")
LABEL = {"status": "Status", "pay_date": "Zahltag", "amount": "Betrag", "currency": "Währung", "ratio": "Verhältnis"}


class GoldenAction(ApiModel):
    instrument_id: str
    action_type: str
    ex_date: date
    status: str
    pay_date: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    ratio: Decimal | None = None
    source: str
    sources: list[str]
    mismatch: list[str] = []  # abweichende Attribute


def _same(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return a is not None and b is not None and Decimal(a) == Decimal(b) or (a is None and b is None)
    return a == b


class CorporateActions:
    def __init__(self, service: LicenseService):
        self.svc = service

    # ---------------------------------------------------------------- Speichern

    def store(self, conn: Connection, instrument_id: str, a: CorporateAction, source: str,
              request_id: str) -> str | None:
        """Bitemporal je (Quelle, Ereignis): 'new', 'changed' oder None (unverändert)."""
        c = db.corporate_action.c
        key = (c.source == source, c.event_key == a.event_key, c.superseded_at.is_(None))
        current = conn.execute(select(db.corporate_action).where(*key)).mappings().first()
        values = {"instrument_id": instrument_id, "action_type": a.action_type, "status": a.status,
                  "ex_date": a.ex_date, "pay_date": a.pay_date, "amount": a.amount, "currency": a.currency,
                  "ratio": a.ratio}
        result = "new"
        if current is not None:
            if all(_same(current[k], v) for k, v in values.items()):
                return None
            conn.execute(update(db.corporate_action).where(*key).values(superseded_at=self.svc.clock()))
            result = "changed"
        conn.execute(insert(db.corporate_action).values(
            row_id=str(uuid.uuid4()), source=source, event_key=a.event_key, request_id=request_id,
            recorded_at=self.svc.clock(), superseded_at=None, **values))
        return result

    def store_all(self, conn: Connection, actions: list[CorporateAction], source: str, request_id: str,
                  user: str) -> dict:
        new = changed = 0
        touched: set[str] = set()
        for a in actions:
            instrument_id = reference.lookup(conn, *self._scheme(a.identifier))
            if instrument_id is None:
                continue
            result = self.store(conn, instrument_id, a, source, request_id)
            if result:
                touched.add(instrument_id)
                new += result == "new"
                changed += result == "changed"
                if a.action_type == "STOCK_SPLIT" or a.status == "CANCELLED":
                    what = "Split" if a.action_type == "STOCK_SPLIT" else a.action_type
                    self.svc.notifier.add(
                        conn, "CORPORATE_ACTION", f"Corporate Action: {what} {a.status.lower()}",
                        f"{a.identifier}: {what} mit Ex-Datum {a.ex_date:%d.%m.%Y}"
                        + (f", Verhältnis {a.ratio.normalize():f}:1" if a.ratio else "") + f" ({source})",
                        {"instrumentId": instrument_id})
        mismatches = self._check(conn, touched)
        if new or changed:
            self.svc._audit(conn, user, "CORPORATE_ACTIONS_STORED", "source", source,
                            {"new": new, "changed": changed, "mismatches": mismatches})
        return {"new": new, "changed": changed, "mismatches": mismatches}

    @staticmethod
    def _scheme(identifier: str) -> tuple[str, str]:
        if identifier.startswith("/"):
            _, scheme, value = identifier.split("/", 2)
            return scheme.upper(), value
        scheme, value = identifier.split(":", 1)
        return scheme.upper(), value

    # ----------------------------------------------------------- Golden Copy

    def _current(self, conn: Connection, instrument_ids: set[str] | None, start: date | None,
                 end: date | None, source: str | None = None) -> list[dict]:
        c = db.corporate_action.c
        conds = [c.superseded_at.is_(None)]
        if instrument_ids is not None:
            conds.append(c.instrument_id.in_(instrument_ids))
        if start:
            conds.append(c.ex_date >= start)
        if end:
            conds.append(c.ex_date <= end)
        if source:
            conds.append(c.source == source.upper())
        return [dict(r) for r in conn.execute(select(db.corporate_action).where(*conds)).mappings().all()]

    def _golden(self, rows: list[dict]) -> list[GoldenAction]:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rows:
            groups[(r["instrument_id"], r["action_type"], r["ex_date"])].append(r)
        result = []
        for (instrument_id, action_type, ex_date), items in groups.items():
            items.sort(key=lambda r: self.svc.source_rank(r["source"]))
            best = items[0]
            mismatch = [k for k in COMPARED if any(not _same(best[k], r[k]) for r in items[1:])]
            result.append(GoldenAction(
                instrument_id=instrument_id, action_type=action_type, ex_date=ex_date, status=best["status"],
                pay_date=best["pay_date"], amount=best["amount"], currency=best["currency"], ratio=best["ratio"],
                source=best["source"], sources=[r["source"] for r in items], mismatch=mismatch))
        return sorted(result, key=lambda g: (g.ex_date, g.instrument_id, g.action_type))

    def _check(self, conn: Connection, instrument_ids: set[str]) -> int:
        if not instrument_ids:
            return 0
        q = db.dq_exception.c
        count = 0
        for g in self._golden(self._current(conn, instrument_ids, None, None)):
            if not g.mismatch:
                continue
            detail = (f"{g.action_type} Ex {g.ex_date:%d.%m.%Y}: Quellen {', '.join(g.sources)} weichen ab bei "
                      + ", ".join(LABEL[m] for m in g.mismatch))
            exists = conn.execute(select(q.exception_id).where(
                q.type == "CA_MISMATCH", q.instrument_id == g.instrument_id, q.field == g.action_type,
                q.value_date == g.ex_date, q.status != "RESOLVED")).first()
            if not exists:
                conn.execute(db.dq_exception.insert().values(
                    exception_id=str(uuid.uuid4()), type="CA_MISMATCH", instrument_id=g.instrument_id,
                    field=g.action_type, value_date=g.ex_date, detail=detail, detected_at=self.svc.clock(),
                    status="OPEN"))
                count += 1
        return count

    def query(self, instrument_id: str | None, start: date | None, end: date | None,
              source: str | None) -> list[GoldenAction]:
        with self.svc.engine.connect() as conn:
            rows = self._current(conn, {instrument_id} if instrument_id else None, start, end, source)
        return self._golden(rows)

    # -------------------------------------------------------------- Abholen

    def fetch_from_bloomberg(self, user: str = "system") -> dict:
        """Tageslauf: Ereignisse der letzten 7 und nächsten 90 Tage für das Universum."""
        today = self.svc.clock().date()
        i = db.instrument.c
        with self.svc.engine.connect() as conn:
            rows = conn.execute(select(db.instrument).where(i.status.in_(
                [InstrumentStatus.ACTIVE, InstrumentStatus.PENDING_UNSUBSCRIBE]))).mappings().all()
        identifiers = []
        for inst in rows:
            for scheme, column in reference.INSTRUMENT_COLUMNS.items():
                if inst[column]:
                    identifiers.append(f"/{scheme.lower()}/{inst[column]}")
                    break
        if not identifiers:
            return {"new": 0, "changed": 0, "mismatches": 0}
        request_id = self.svc.bloomberg.request_corporate_actions(identifiers, today - timedelta(days=7),
                                                                  today + timedelta(days=90))
        delivery = self.svc.bloomberg.fetch(request_id)
        if delivery is None:
            return {"pending": request_id}
        with self.svc.engine.begin() as conn:
            return self.store_all(conn, delivery.actions, "BLOOMBERG", request_id, user)

    def import_csv(self, vendor: str, text: str, file_name: str, user: str) -> dict:
        """CSV: Kennungstyp;Kennung;EreignisId;Art;Status;ExDatum;Zahltag;Betrag;Waehrung;Verhaeltnis"""
        vendor = check_vendor(vendor)
        required = ["Kennungstyp", "Kennung", "EreignisId", "Art", "Status", "ExDatum"]
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=self.svc.settings.xentis_delimiter)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise Invalid(f"Spalten fehlen: {', '.join(missing)}")
        actions, skipped = [], []
        rows = list(reader)
        for n, r in enumerate(rows, start=2):
            kind, status = r["Art"].strip().upper(), r["Status"].strip().upper()
            if kind not in TYPES or status not in STATUSES:
                skipped.append({"line": n, "reason": f"Art oder Status unbekannt ({kind}, {status})"})
                continue
            try:
                ex = _parse_date(r["ExDatum"])
                pay = _parse_date(r["Zahltag"]) if (r.get("Zahltag") or "").strip() else None
            except ValueError:
                skipped.append({"line": n, "reason": "Datum nicht lesbar"})
                continue
            amount, ratio = parse_value(r.get("Betrag") or ""), parse_value(r.get("Verhaeltnis") or "")
            actions.append(CorporateAction(
                identifier=f"{r['Kennungstyp'].strip().upper()}:{r['Kennung'].strip()}",
                event_key=r["EreignisId"].strip(), action_type=kind, status=status, ex_date=ex, pay_date=pay,
                amount=amount if isinstance(amount, Decimal) else None,
                currency=(r.get("Waehrung") or "").strip().upper() or None,
                ratio=ratio if isinstance(ratio, Decimal) else None))
        with self.svc.engine.begin() as conn:
            if rows:
                conn.execute(insert(db.vendor_file_raw), [
                    {"vendor": vendor, "kind": "corporate-actions", "file_name": file_name,
                     "received_at": self.svc.clock(), "line_no": n, "payload": json.dumps(r, ensure_ascii=False)}
                    for n, r in enumerate(rows, start=2)])
            unknown = [a for a in actions if reference.lookup(conn, *self._scheme(a.identifier)) is None]
            skipped += [{"line": None, "reason": f"Kennung unbekannt: {a.identifier}"} for a in unknown]
            result = self.store_all(conn, [a for a in actions if a not in unknown], vendor,
                                    f"{vendor.lower()}:{file_name}", user)
        return {"vendor": vendor, **result, "skipped": skipped}
