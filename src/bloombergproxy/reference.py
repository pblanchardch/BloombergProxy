"""Stammdaten (A1) und ID-Abgleich (A4).

Bloomberg-Stammdatenfelder befüllen das verknüpfte Modell Emittent -> Instrument -> Listing
(silver.issuer, silver.security, silver.listing) und die Zuordnungstabelle aller Kennungen
(silver.identifier_xref). Über die Zuordnung findet der Service ein Instrument unter jeder
bekannten Kennung – etwa eine per ISIN bestellte Aktie auch unter ihrer FIGI oder der
Kennung eines anderen Anbieters. Zeigt dieselbe Kennung auf zwei Instrumente, entsteht eine
Datenqualitäts-Ausnahme ID_CONFLICT; die Zuordnung wird dann nicht geändert.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Connection, Table, and_, insert, select, update

from . import db

# Bloomberg-Feld -> Kennungsschema in der Zuordnungstabelle
XREF_FIELDS = {"ID_ISIN": "ISIN", "ID_BB_GLOBAL": "FIGI", "ID_CUSIP": "CUSIP", "ID_SEDOL1": "SEDOL",
               "PARSEKYABLE_DES": "TICKER"}
# Kennungsschema -> Spalte in app.instrument
INSTRUMENT_COLUMNS = {"ISIN": "isin", "FIGI": "figi", "CUSIP": "cusip", "SEDOL": "sedol", "TICKER": "ticker"}
REFERENCE_FIELDS = set(XREF_FIELDS) | {
    "COMPOSITE_FIGI", "EXCH_CODE", "CRNCY", "ID_BB_COMPANY", "ISSUER", "LEGAL_ENTITY_IDENTIFIER",
    "CNTRY_OF_DOMICILE", "INDUSTRY_SECTOR", "SECURITY_TYP", "NAME",
}


def _upsert(conn: Connection, table: Table, key: dict, values: dict) -> None:
    """Portables Upsert (SQLite und Databricks), ohne Nullwerte zu überschreiben."""
    values = {k: v for k, v in values.items() if v is not None}
    cond = and_(*(table.c[k] == v for k, v in key.items()))
    if conn.execute(select(*(table.c[k] for k in key)).where(cond)).first():
        if values:
            conn.execute(update(table).where(cond).values(**values))
    else:
        conn.execute(insert(table).values(**key, **values))


def lookup(conn: Connection, scheme: str, value: str) -> str | None:
    """Instrument zu einer Kennung: zuerst app.instrument, dann die Zuordnungstabelle."""
    column = INSTRUMENT_COLUMNS.get(scheme)
    if column:
        found = conn.execute(select(db.instrument.c.instrument_id)
                             .where(db.instrument.c[column] == value)).scalar()
        if found:
            return found
    x = db.identifier_xref.c
    return conn.execute(select(x.instrument_id).where(x.scheme == scheme, x.value == value)).scalar()


def add_xref(conn: Connection, instrument_id: str, scheme: str, value: str, source: str,
             now: datetime) -> str | None:
    """Ordnet eine Kennung zu. Gibt bei Konflikt die ID des anderen Instruments zurück."""
    other = lookup(conn, scheme, value)
    if other and other != instrument_id:
        return other
    _upsert(conn, db.identifier_xref, {"scheme": scheme, "value": value, "source": source},
            {"instrument_id": instrument_id, "last_seen": now})
    x = db.identifier_xref.c
    conn.execute(update(db.identifier_xref).where(
        x.scheme == scheme, x.value == value, x.source == source, x.first_seen.is_(None)).values(first_seen=now))
    column = INSTRUMENT_COLUMNS.get(scheme)
    if column:
        # Fehlende Kennungen am Instrument ergänzen: erkennt Duplikate auch über Schemas hinweg (B3).
        i = db.instrument.c
        conn.execute(update(db.instrument).where(i.instrument_id == instrument_id, i[column].is_(None))
                     .values(**{column: value}))
    return None


def conflict(conn: Connection, instrument_id: str, scheme: str, value: str, other: str, day: date,
             now: datetime) -> dict:
    q = db.dq_exception.c
    detail = f"{scheme} {value} gehört bereits zu Instrument {other}"
    exists = conn.execute(select(q.exception_id).where(
        q.type == "ID_CONFLICT", q.instrument_id == instrument_id, q.field == scheme, q.detail == detail)).first()
    row = {"exception_id": str(uuid.uuid4()), "type": "ID_CONFLICT", "instrument_id": instrument_id,
           "field": scheme, "value_date": day, "detail": detail, "detected_at": now, "status": "OPEN"}
    if not exists:
        conn.execute(db.dq_exception.insert().values(**row))
    return row


def apply(conn: Connection, instrument_id: str, values: dict, source: str, day: date, now: datetime) -> list[dict]:
    """Übernimmt Stammdatenfelder eines Instruments; gibt ID-Konflikte zurück."""
    company = values.get("ID_BB_COMPANY")
    issuer_id = str(company) if company is not None else None
    if issuer_id:
        _upsert(conn, db.issuer, {"issuer_id": issuer_id}, {
            "name": values.get("ISSUER"), "lei": values.get("LEGAL_ENTITY_IDENTIFIER"),
            "country": values.get("CNTRY_OF_DOMICILE"), "industry": values.get("INDUSTRY_SECTOR")})
    _upsert(conn, db.security, {"instrument_id": instrument_id}, {
        "issuer_id": issuer_id, "security_type": values.get("SECURITY_TYP"), "currency": values.get("CRNCY"),
        "composite_figi": values.get("COMPOSITE_FIGI")})
    if values.get("ID_BB_GLOBAL"):
        _upsert(conn, db.listing, {"figi": values["ID_BB_GLOBAL"]}, {
            "instrument_id": instrument_id, "exchange_code": values.get("EXCH_CODE"),
            "ticker": values.get("PARSEKYABLE_DES"), "currency": values.get("CRNCY"), "is_primary": True})
    if values.get("NAME"):
        conn.execute(update(db.instrument).where(db.instrument.c.instrument_id == instrument_id)
                     .values(name=values["NAME"]))

    conflicts = []
    for field, scheme in XREF_FIELDS.items():
        value = values.get(field)
        if not value:
            continue
        other = add_xref(conn, instrument_id, scheme, str(value), source, now)
        if other:
            conflicts.append(conflict(conn, instrument_id, scheme, str(value), other, day, now))
    return conflicts
