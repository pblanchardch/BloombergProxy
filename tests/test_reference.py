from sqlalchemy import select

from bloombergproxy import db, reference
from conftest import ALICE, subscribe, valid_isins

ISIN = valid_isins(1)


def deliver(client, service, field_sets=("PRICING_EOD", "REFERENCE_FULL"), instruments=ISIN):
    order = subscribe(client, instruments, field_sets=field_sets).json()
    service.process_pending_deliveries()
    return order


def test_reference_data_fills_issuer_security_listing(client, service):
    deliver(client, service)
    inst = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]
    schemes = {i["type"] for i in inst["identifiers"]}
    assert {"ISIN", "FIGI", "SEDOL", "TICKER"} <= schemes
    with service.engine.connect() as conn:
        sec = conn.execute(select(db.security)).mappings().one()
        issuer = conn.execute(select(db.issuer)).mappings().one()
        listing = conn.execute(select(db.listing)).mappings().one()
    assert sec["instrument_id"] == inst["id"] and sec["issuer_id"] == issuer["issuer_id"]
    assert issuer["name"].startswith("Mock Emittent") and len(issuer["lei"]) == 20
    assert listing["exchange_code"] == "SW" and listing["figi"].startswith("BBG")


def test_order_by_figi_is_recognised_as_duplicate(client, service):
    deliver(client, service)
    figi = next(i["value"] for i in client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["identifiers"]
                if i["type"] == "FIGI")
    est = client.post("/api/v1/orders/estimate", headers=ALICE, json={
        "type": "SUBSCRIBE", "instruments": [{"type": "FIGI", "value": figi}], "fieldSets": ["PRICING_EOD"]}).json()
    assert est["duplicates"] == [{"type": "FIGI", "value": figi}]


def test_identifier_conflict_is_reported_not_overwritten(client, service):
    deliver(client, service)
    inst_id = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]
    # Ein zweites Instrument, dem die Lieferung dieselbe FIGI zuordnen würde
    with service.engine.begin() as conn:
        figi = conn.execute(select(db.instrument.c.figi)).scalar()
        other = reference.add_xref(conn, "anderes-instrument", "FIGI", figi, "VENDOR_X", service.clock())
    assert other == inst_id
    with service.engine.begin() as conn:
        rows = reference.apply(conn, "anderes-instrument", {"ID_BB_GLOBAL": figi}, "VENDOR_X",
                               service.clock().date(), service.clock())
    assert rows[0]["type"] == "ID_CONFLICT"
    exc = client.get("/api/v1/data-quality/exceptions?status=OPEN", headers=ALICE).json()
    assert [e["type"] for e in exc] == ["ID_CONFLICT"]
