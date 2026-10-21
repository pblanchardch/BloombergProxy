from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from bloombergproxy import db
from bloombergproxy.bloomberg import MockBloombergClient, valid_isin
from bloombergproxy.config import Settings
from bloombergproxy.main import create_app
from bloombergproxy.service import LicenseService

REFERENCE_FULL = ["ID_ISIN", "ID_BB_GLOBAL", "COMPOSITE_FIGI", "ID_CUSIP", "ID_SEDOL1", "PARSEKYABLE_DES",
                  "EXCH_CODE", "CRNCY", "ID_BB_COMPANY", "ISSUER", "LEGAL_ENTITY_IDENTIFIER",
                  "CNTRY_OF_DOMICILE", "INDUSTRY_SECTOR", "SECURITY_TYP"]

# Testpreise, nicht die Vertragswerte.
PRICES = {"Pricing": Decimal("1.50"), "SecurityMaster": Decimal("0.80")}


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return Clock(datetime(2026, 10, 21, 10, 0))


@pytest.fixture
def bloomberg(clock):
    return MockBloombergClient(clock=clock)


@pytest.fixture
def service(clock, bloomberg):
    settings = Settings(database_url="sqlite://", auth_mode="header")
    engine = db.make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(insert(db.field_catalog), [
            {"mnemonic": "PX_LAST", "description": "Letzter Preis", "category": "Pricing"},
            {"mnemonic": "PX_VOLUME", "description": "Volumen", "category": "Pricing"},
            {"mnemonic": "NAME", "description": "Name", "category": "SecurityMaster"},
            {"mnemonic": "ESG_SCORE", "description": "ESG-Score", "category": "ESG"},
        ] + [{"mnemonic": m, "description": m, "category": "SecurityMaster"} for m in REFERENCE_FULL])
        conn.execute(insert(db.field_set), [
            {"field_set_id": "PRICING_EOD", "name": "Preise Tagesende"},
            {"field_set_id": "PRICING_VOL", "name": "Nur Volumen"},
            {"field_set_id": "REFERENCE", "name": "Stammdaten"},
            {"field_set_id": "ESG", "name": "ESG"},
            {"field_set_id": "REFERENCE_FULL", "name": "Stammdaten vollständig"},
        ])
        conn.execute(insert(db.field_set_field), [
            {"field_set_id": "PRICING_EOD", "mnemonic": "PX_LAST"},
            {"field_set_id": "PRICING_EOD", "mnemonic": "PX_VOLUME"},
            {"field_set_id": "PRICING_VOL", "mnemonic": "PX_VOLUME"},
            {"field_set_id": "REFERENCE", "mnemonic": "NAME"},
            {"field_set_id": "ESG", "mnemonic": "ESG_SCORE"},
        ] + [{"field_set_id": "REFERENCE_FULL", "mnemonic": m} for m in ["NAME", *REFERENCE_FULL]])
        conn.execute(insert(db.pricing_category), [
            {"category": c, "valid_from": date(2026, 1, 1), "price_per_instrument": p, "currency": "CHF"}
            for c, p in PRICES.items()
        ])
    return LicenseService(engine, bloomberg, settings, clock=clock)


@pytest.fixture
def client(service):
    return TestClient(create_app(Settings(auth_mode="header"), service=service))


def as_user(name: str, *scopes: str) -> dict:
    return {"X-User": name, "X-Scopes": ",".join(scopes or ("lizenz.read", "lizenz.order"))}


ALICE = as_user("alice")
BOB_APPROVER = as_user("bob", "lizenz.read", "lizenz.approve")
ADMIN = as_user("admin", "lizenz.read", "lizenz.admin")


def isins(n: int, start: int = 0) -> list[dict]:
    return [{"type": "ISIN", "value": f"CH{i:010d}"} for i in range(start, start + n)]


def valid_isins(n: int) -> list[dict]:
    """ISINs mit korrekter Prüfziffer (die übrigen Test-ISINs meldet der Mock als ungültig)."""
    result = []
    for i in range(n):
        stem = f"CH{i:09d}"
        result.append({"type": "ISIN", "value": next(stem + str(d) for d in range(10) if valid_isin(stem + str(d)))})
    return result


def subscribe(client, instruments, field_sets=("PRICING_EOD",), headers=ALICE, **extra):
    return client.post("/api/v1/orders", headers=headers, json={
        "type": "SUBSCRIBE", "instruments": instruments, "fieldSets": list(field_sets), **extra})
