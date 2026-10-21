"""Beispieldaten für die lokale Entwicklung: ``python -m bloombergproxy.devseed``.

Felder, Kategorien und Preise sind Platzhalter, nicht die Werte aus dem Bloomberg-Vertrag.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select

from . import db
from .config import Settings

FIELDS = [
    ("PX_LAST", "Letzter Preis", "Pricing"),
    ("PX_VOLUME", "Volumen", "Pricing"),
    ("NAME", "Name", "SecurityMaster"),
    ("CRNCY", "Währung", "SecurityMaster"),
]
FIELD_SETS = {
    "PRICING_EOD": ("Preise Tagesende", ["PX_LAST", "PX_VOLUME"]),
    "REFERENCE": ("Stammdaten", ["NAME", "CRNCY"]),
}
PRICES = {"Pricing": Decimal("1.50"), "SecurityMaster": Decimal("0.80")}


def main() -> None:
    engine = db.make_engine(Settings().database_url)
    with engine.begin() as conn:
        if conn.execute(select(db.field_catalog.c.mnemonic).limit(1)).first():
            print("Beispieldaten sind bereits vorhanden.")
            return
        conn.execute(insert(db.field_catalog), [
            {"mnemonic": m, "description": d, "category": c} for m, d, c in FIELDS])
        conn.execute(insert(db.field_set), [
            {"field_set_id": k, "name": name} for k, (name, _) in FIELD_SETS.items()])
        conn.execute(insert(db.field_set_field), [
            {"field_set_id": k, "mnemonic": m} for k, (_, fields) in FIELD_SETS.items() for m in fields])
        conn.execute(insert(db.pricing_category), [
            {"category": c, "valid_from": date(2026, 1, 1), "price_per_instrument": p, "currency": "CHF"}
            for c, p in PRICES.items()])
    print("Beispieldaten angelegt.")


if __name__ == "__main__":
    main()
