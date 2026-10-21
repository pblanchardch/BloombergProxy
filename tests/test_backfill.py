from datetime import date

from sqlalchemy import insert

from bloombergproxy import db
from conftest import ALICE, BOB_APPROVER, PRICES, valid_isins

ISIN = valid_isins(1)


def backfill(client, instruments=ISIN, fields=("PX_LAST",), start="2026-10-12", end="2026-10-16", headers=ALICE):
    return client.post("/api/v1/orders", headers=headers, json={
        "type": "BACKFILL", "instruments": instruments, "fields": list(fields), "from": start, "to": end})


def subscribe_and_deliver(client, service):
    client.post("/api/v1/orders", headers=ALICE, json={
        "type": "SUBSCRIBE", "instruments": ISIN, "fieldSets": ["PRICING_EOD"]})
    service.process_pending_deliveries()
    return client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]


def test_backfill_loads_weekdays_into_timeseries(client, service):
    inst_id = subscribe_and_deliver(client, service)
    order = backfill(client).json()
    assert order["type"] == "BACKFILL"
    assert (order["from"], order["to"], order["fields"]) == ("2026-10-12", "2026-10-16", ["PX_LAST"])
    # Kategorie History hat in den Testdaten keinen Preis -> Freigabe nötig
    assert order["estimate"]["unpricedCategories"] == ["History"]
    assert order["status"] == "PENDING_APPROVAL"

    client.post(f"/api/v1/orders/{order['id']}/approve", headers=BOB_APPROVER)
    [result] = service.process_pending_deliveries()
    assert result["status"] == "COMPLETED"
    assert result["values"] == 5  # Mo-Fr

    ts = client.get(f"/api/v1/timeseries?instrumentId={inst_id}&field=PX_LAST&from=2026-10-01&to=2026-10-31",
                    headers=ALICE).json()
    assert [p["date"] for p in ts["points"]] == [
        "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-21"]


def test_priced_small_backfill_runs_without_approval_and_is_billed_once(client, service):
    with service.engine.begin() as conn:
        conn.execute(insert(db.pricing_category).values(
            category="History", valid_from=date(2026, 1, 1), price_per_instrument=PRICES["Pricing"], currency="CHF"))
    subscribe_and_deliver(client, service)
    first = backfill(client).json()
    assert first["status"] == "SUBMITTED"
    assert first["estimate"]["additionalMonthlyCost"] == "1.50"
    second = backfill(client, start="2026-09-01", end="2026-09-30").json()
    assert second["estimate"]["additionalMonthlyCost"] == "0"
    assert second["estimate"]["alreadyBilledInstruments"] == 1


def test_backfill_needs_instrument_in_universe(client):
    r = backfill(client)
    assert r.status_code == 400
    assert "nicht im Universum" in r.json()["message"]


def test_backfill_validation(client, service):
    subscribe_and_deliver(client, service)
    assert "Zeitraum" in backfill(client, start="2026-10-16", end="2026-10-12").json()["message"]
    assert "Zukunft" in backfill(client, end="2026-12-31").json()["message"]
    assert "Unbekannte Felder" in backfill(client, fields=["NOPE"]).json()["message"]
    assert "Feld" in backfill(client, fields=[]).json()["message"]
