"""Daten anderer Anbieter (A4).

Zwei CSV-Importe je Anbieter (Trennzeichen wie bei Xentis, Standard Semikolon):

* Zuordnung – ``Kennungstyp;Kennung;ISIN``: verknüpft eine Anbieter-Kennung (z. B. MSCI_ID)
  über die ISIN mit einem Instrument;
* Werte – ``Kennungstyp;Kennung;Datum;Feld;Wert``: Kennung in jedem bekannten Schema
  (ISIN, FIGI, … oder eine zugeordnete Anbieter-Kennung).

Werte landen bitemporal in silver.timeseries_value mit dem Anbieter als Quelle. Die Golden Copy
wählt je Tag den Wert der Quelle mit dem höchsten Rang (``source_precedence``).
"""

import csv
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import insert

from . import db, reference
from .reconcile import _parse_date
from .service import Invalid, LicenseService

VENDOR = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")


def check_vendor(vendor: str) -> str:
    vendor = vendor.upper()
    if not VENDOR.match(vendor) or vendor == "BLOOMBERG":
        raise Invalid("Anbietername: 2–20 Zeichen A–Z, 0–9, _; BLOOMBERG ist reserviert")
    return vendor


def parse_value(text: str) -> Decimal | str | None:
    text = text.strip()
    if not text:
        return None
    try:
        return Decimal(text.replace("'", "").replace("’", ""))
    except InvalidOperation:
        return text


class VendorImport:
    def __init__(self, service: LicenseService):
        self.svc = service

    def _rows(self, text: str, required: list[str]) -> list[dict]:
        reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=self.svc.settings.xentis_delimiter)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise Invalid(f"Spalten fehlen: {', '.join(missing)}")
        return list(reader)

    def _raw(self, conn, vendor: str, kind: str, file_name: str, rows: list[dict]) -> None:
        now = self.svc.clock()
        if rows:
            conn.execute(insert(db.vendor_file_raw), [
                {"vendor": vendor, "kind": kind, "file_name": file_name, "received_at": now, "line_no": n,
                 "payload": json.dumps(r, ensure_ascii=False)} for n, r in enumerate(rows, start=2)])

    def import_xref(self, vendor: str, text: str, file_name: str, user: str) -> dict:
        vendor = check_vendor(vendor)
        rows = self._rows(text, ["Kennungstyp", "Kennung", "ISIN"])
        mapped, skipped = 0, []
        with self.svc.engine.begin() as conn:
            self._raw(conn, vendor, "xref", file_name, rows)
            for n, r in enumerate(rows, start=2):
                scheme, value, isin = r["Kennungstyp"].strip().upper(), r["Kennung"].strip(), r["ISIN"].strip().upper()
                instrument_id = reference.lookup(conn, "ISIN", isin)
                if not (scheme and value) or instrument_id is None:
                    skipped.append({"line": n, "reason": "ISIN unbekannt" if scheme and value else "Kennung fehlt"})
                    continue
                other = reference.add_xref(conn, instrument_id, scheme, value, vendor, self.svc.clock())
                if other:
                    reference.conflict(conn, instrument_id, scheme, value, other, self.svc.clock().date(),
                                       self.svc.clock())
                    skipped.append({"line": n, "reason": f"{scheme} {value} gehört zu einem anderen Instrument"})
                    continue
                mapped += 1
            self.svc._audit(conn, user, "VENDOR_XREF_IMPORTED", "vendor", vendor, {"mapped": mapped})
        return {"vendor": vendor, "mapped": mapped, "skipped": skipped}

    def import_values(self, vendor: str, text: str, file_name: str, user: str) -> dict:
        vendor = check_vendor(vendor)
        rows = self._rows(text, ["Kennungstyp", "Kennung", "Datum", "Feld", "Wert"])
        written = corrected = 0
        skipped: list[dict] = []
        request_id = f"{vendor.lower()}:{file_name}"
        with self.svc.engine.begin() as conn:
            self._raw(conn, vendor, "values", file_name, rows)
            for n, r in enumerate(rows, start=2):
                instrument_id = reference.lookup(conn, r["Kennungstyp"].strip().upper(), r["Kennung"].strip())
                if instrument_id is None:
                    skipped.append({"line": n, "reason": "Kennung unbekannt"})
                    continue
                try:
                    day: date = _parse_date(r["Datum"])
                except ValueError:
                    skipped.append({"line": n, "reason": "Datum nicht lesbar"})
                    continue
                field = r["Feld"].strip().upper()
                result = self.svc.write_value(conn, instrument_id, field, day, parse_value(r["Wert"]), vendor,
                                              request_id)
                written += result is not None
                corrected += result == "correction"
            self.svc._audit(conn, user, "VENDOR_VALUES_IMPORTED", "vendor", vendor,
                            {"file": file_name, "values": written, "corrections": corrected})
        return {"vendor": vendor, "values": written, "corrections": corrected, "skipped": skipped}
