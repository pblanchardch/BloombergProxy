from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import insert

from bloombergproxy import db
from conftest import ADMIN, ALICE, as_user

RULES = [
    {"category": "ESG", "requiredRole": "daten.esg", "redistributable": False, "note": "Vertrag ESG"},
    {"category": "Pricing", "requiredRole": None, "redistributable": False},
]
ESG_READER = as_user("esg-team", "lizenz.read", "daten.esg")


def seed(service):
    with service.engine.begin() as conn:
        conn.execute(insert(db.timeseries_value), [
            {"instrument_id": "i1", "field": f, "value_date": date(2026, 10, 20), "value_num": Decimal("5"),
             "value_str": None, "recorded_at": datetime(2026, 10, 20, 18), "superseded_at": None,
             "bbg_request_id": "t"} for f in ("ESG_SCORE", "PX_LAST", "NAME")])


def ts(client, field, headers, fmt="json"):
    return client.get(f"/api/v1/timeseries?instrumentId=i1&field={field}&from=2026-10-01&to=2026-10-31"
                      f"&format={fmt}", headers=headers)


def test_rules_are_admin_only_and_roundtrip(client):
    assert client.put("/api/v1/entitlements", headers=ALICE, json=RULES).status_code == 403
    assert client.put("/api/v1/entitlements", headers=ADMIN, json=RULES).status_code == 200
    stored = client.get("/api/v1/entitlements", headers=ALICE).json()
    assert stored[0] == RULES[0]


def test_restricted_category_needs_role(client, service):
    seed(service)
    client.put("/api/v1/entitlements", headers=ADMIN, json=RULES)
    denied = ts(client, "ESG_SCORE", ALICE)
    assert denied.status_code == 403
    assert "daten.esg" in denied.json()["message"]
    assert ts(client, "ESG_SCORE", ESG_READER).status_code == 200
    # Kategorie ohne Regel bleibt frei
    assert ts(client, "NAME", ALICE).status_code == 200


def test_not_redistributable_blocks_export_only(client, service):
    seed(service)
    client.put("/api/v1/entitlements", headers=ADMIN, json=RULES)
    assert ts(client, "PX_LAST", ALICE).status_code == 200
    r = ts(client, "PX_LAST", ALICE, fmt="csv")
    assert r.status_code == 403
    assert "Weitergabe" in r.json()["message"]
    assert ts(client, "NAME", ALICE, fmt="csv").status_code == 200


def test_usage_report_lists_who_accessed_restricted_data(client, service):
    seed(service)
    client.put("/api/v1/entitlements", headers=ADMIN, json=RULES)
    ts(client, "ESG_SCORE", ESG_READER)
    ts(client, "ESG_SCORE", ESG_READER)
    ts(client, "PX_LAST", ALICE)
    ts(client, "ESG_SCORE", ALICE)  # verweigert, zählt nicht als Nutzung
    usage = client.get("/api/v1/entitlements/usage?month=2026-10", headers=ADMIN).json()
    assert usage == [{"category": "ESG", "users": ["esg-team"], "accesses": 2},
                     {"category": "Pricing", "users": ["alice"], "accesses": 1}]
    denied = [a for a in client.get("/api/v1/audit", headers=ALICE).json() if a["action"] == "DATA_ACCESS_DENIED"]
    assert denied[0]["user"] == "alice"


def test_field_catalog_shows_entitlement(client):
    client.put("/api/v1/entitlements", headers=ADMIN, json=RULES)
    fields = {f["mnemonic"]: f for f in client.get("/api/v1/fields", headers=ALICE).json()}
    assert fields["ESG_SCORE"]["entitlement"] == {"requiredRole": "daten.esg", "redistributable": False,
                                                  "allowed": False}
    assert fields["NAME"]["entitlement"] is None
