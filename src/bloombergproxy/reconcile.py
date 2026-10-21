"""Universum folgt den Xentis-Positionen (B1).

Xentis liefert einmal pro Tag die Positionen. Der Abgleich vergleicht den letzten Stand mit
dem Universum:

* gehaltene ISINs, die nicht (mehr) im Universum sind, werden mit den Standard-Feldlisten
  bestellt – über den normalen Bestellweg, also mit Kostenprognose und ab 200 CHF mit
  Freigabe durch eine Person;
* Instrumente, die der Abgleich bestellt hat (Quelle XENTIS) und die nicht mehr gehalten
  werden, werden abbestellt – wirksam zum Monatsende;
* manuell bestellte Instrumente fasst der Abgleich nie an.
"""

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import delete, func, insert, select

from . import db
from .models import Identifier, IdentifierType, InstrumentStatus, OrderRequest, OrderType
from .service import Invalid, LicenseService, NotFound

SYSTEM_USER = "xentis-abgleich"


def _parse_date(text: str) -> date:
    text = text.strip()
    if "." in text:
        d, m, y = text.split(".")
        return date(int(y), int(m), int(d))
    return date.fromisoformat(text)


def _parse_quantity(text: str) -> Decimal:
    # Schweizer Schreibweise: 1'250.50
    return Decimal(text.strip().replace("'", "").replace("’", "").replace(" ", "") or "0")


class Reconciler:
    def __init__(self, service: LicenseService):
        self.svc = service
        self.settings = service.settings

    # ------------------------------------------------------------------ Import

    def import_positions(self, text: str, file_name: str, user: str) -> dict:
        s = self.settings
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=s.xentis_delimiter)
        required = [s.xentis_col_date, s.xentis_col_portfolio, s.xentis_col_isin, s.xentis_col_quantity]
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise Invalid(f"Spalten fehlen in der Positionsdatei: {', '.join(missing)}")

        now = self.svc.clock()
        raw, rows, skipped = [], [], []
        for n, line in enumerate(reader, start=2):  # Zeile 1 = Kopfzeile
            raw.append(line)
            isin = (line[s.xentis_col_isin] or "").strip().upper()
            if not isin:
                skipped.append({"line": n, "reason": "ohne ISIN"})
                continue
            try:
                rows.append({"as_of_date": _parse_date(line[s.xentis_col_date]),
                             "portfolio": line[s.xentis_col_portfolio].strip(), "isin": isin,
                             "quantity": _parse_quantity(line[s.xentis_col_quantity])})
            except (ValueError, InvalidOperation):
                skipped.append({"line": n, "reason": "Datum oder Bestand nicht lesbar"})
        if not rows:
            raise Invalid("Die Positionsdatei enthält keine verwertbaren Zeilen")

        dates = sorted({r["as_of_date"] for r in rows})
        i = db.instrument.c
        with self.svc.engine.begin() as conn:
            conn.execute(insert(db.xentis_positions_raw), [
                {"delivery_date": now.date(), "file_name": file_name, "received_at": now, "line_no": n,
                 "payload": json.dumps(line, ensure_ascii=False)} for n, line in enumerate(raw, start=2)])
            ids = dict(conn.execute(select(i.isin, i.instrument_id).where(
                i.isin.in_({r["isin"] for r in rows}))).all())
            # Eine erneute Lieferung für denselben Stichtag ersetzt die frühere.
            conn.execute(delete(db.position).where(db.position.c.as_of_date.in_(dates)))
            conn.execute(insert(db.position), [r | {"instrument_id": ids.get(r["isin"])} for r in rows])
            self.svc._audit(conn, user, "POSITIONS_IMPORTED", "positions", file_name,
                            {"dates": dates, "rows": len(rows), "skipped": len(skipped)})
        return {"asOfDates": dates, "rows": len(rows), "skipped": skipped}

    # --------------------------------------------------------------- Abgleich

    def reconcile(self, dry_run: bool = True, user: str = SYSTEM_USER) -> dict:
        p, i = db.position.c, db.instrument.c
        started = self.svc.clock()
        with self.svc.engine.connect() as conn:
            as_of = conn.execute(select(func.max(p.as_of_date))).scalar()
            if as_of is None:
                raise Invalid("Es sind noch keine Xentis-Positionen importiert")
            held = set(conn.execute(
                select(p.isin).where(p.as_of_date == as_of).group_by(p.isin).having(func.sum(p.quantity) != 0)
            ).scalars())
            instruments = conn.execute(select(i.instrument_id, i.isin, i.status, i.source)
                                       .where(i.isin.is_not(None))).mappings().all()

        by_isin = {r["isin"]: r for r in instruments}
        proposals = []
        for isin in sorted(held):
            inst = by_isin.get(isin)
            if inst is None:
                proposals.append({"isin": isin, "instrument_id": None, "action": "SUBSCRIBE",
                                  "reason": "Neue Position"})
            elif inst["status"] in (InstrumentStatus.INACTIVE, InstrumentStatus.PENDING_UNSUBSCRIBE):
                proposals.append({"isin": isin, "instrument_id": inst["instrument_id"], "action": "SUBSCRIBE",
                                  "reason": "Wieder gehalten"})
        xentis_active = [r for r in instruments
                         if r["source"] == "XENTIS" and r["status"] == InstrumentStatus.ACTIVE]
        to_remove = [r for r in xentis_active if r["isin"] not in held]
        proposals += [{"isin": r["isin"], "instrument_id": r["instrument_id"], "action": "UNSUBSCRIBE",
                       "reason": "Position geschlossen"} for r in to_remove]

        share = len(to_remove) / len(xentis_active) if xentis_active else 0.0
        blocked = share > self.settings.xentis_max_unsubscribe_share
        notes = []
        if blocked:
            notes.append(f"Sicherung: {len(to_remove)} von {len(xentis_active)} Xentis-Instrumenten würden "
                         f"abbestellt ({share:.0%}); Abbestellungen nicht ausgeführt. Positionsdatei prüfen.")

        orders = {}
        if not dry_run:
            subscribe = [x["isin"] for x in proposals if x["action"] == "SUBSCRIBE"]
            unsubscribe = [x["isin"] for x in proposals if x["action"] == "UNSUBSCRIBE"]
            if subscribe:
                orders["SUBSCRIBE"] = self._order(OrderType.SUBSCRIBE, subscribe, as_of)
            if unsubscribe and not blocked:
                orders["UNSUBSCRIBE"] = self._order(OrderType.UNSUBSCRIBE, unsubscribe, as_of)

        job_id = str(uuid.uuid4())
        summary = {
            "jobId": job_id, "asOf": as_of, "dryRun": dry_run, "held": len(held),
            "subscribe": sum(x["action"] == "SUBSCRIBE" for x in proposals),
            "unsubscribe": len(to_remove), "unsubscribeBlocked": blocked, "notes": notes,
            "orders": {k: {"id": o.id, "status": o.status} for k, o in orders.items()},
        }
        with self.svc.engine.begin() as conn:
            conn.execute(insert(db.job).values(
                job_id=job_id, kind="RECONCILE", status="FAILED" if blocked and not dry_run else "SUCCEEDED",
                started_at=started, finished_at=self.svc.clock(), summary=json.dumps(summary, default=str)))
            if proposals:
                conn.execute(insert(db.reconcile_proposal), [
                    {"job_id": job_id, "instrument_id": x["instrument_id"], "isin": x["isin"],
                     "action": x["action"], "reason": x["reason"],
                     "order_id": orders[x["action"]].id if x["action"] in orders else None}
                    for x in proposals])
            self.svc._audit(conn, user, "UNIVERSE_RECONCILED", "job", job_id, summary)
        return summary | {"proposals": [
            {"isin": x["isin"], "action": x["action"], "reason": x["reason"]} for x in proposals]}

    def _order(self, order_type: OrderType, isin_list: list[str], as_of: date):
        req = OrderRequest(
            type=order_type,
            instruments=[Identifier(type=IdentifierType.ISIN, value=v) for v in isin_list],
            field_sets=self.settings.xentis_field_sets if order_type == OrderType.SUBSCRIBE else [],
            reason=f"Xentis-Abgleich, Positionen per {as_of.isoformat()}",
        )
        return self.svc.create_order(req, SYSTEM_USER, source="XENTIS")

    def get_job(self, job_id: str) -> dict:
        with self.svc.engine.connect() as conn:
            row = conn.execute(select(db.job).where(db.job.c.job_id == job_id)).mappings().first()
            if row is None:
                raise NotFound(job_id)
            proposals = conn.execute(select(db.reconcile_proposal).where(
                db.reconcile_proposal.c.job_id == job_id)).mappings().all()
        result = {"id": row["job_id"], "kind": row["kind"], "status": row["status"],
                  "startedAt": row["started_at"], "finishedAt": row["finished_at"],
                  "summary": json.loads(row["summary"]) if row["summary"] else None}
        if proposals:
            result["proposals"] = [{"isin": x["isin"], "action": x["action"], "reason": x["reason"],
                                    "orderId": x["order_id"]} for x in proposals]
        return result

