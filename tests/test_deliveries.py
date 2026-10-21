from datetime import date, datetime

from bloombergproxy.bloomberg import MockBloombergClient, valid_isin
from conftest import ADMIN, ALICE, isins, subscribe, valid_isins


def test_isin_check_digit():
    assert valid_isin("CH0012032048")
    assert valid_isin("US0378331005")
    assert not valid_isin("CH0012032049")
    assert not valid_isin("CH001203204")


def test_mock_is_deterministic(clock):
    a = MockBloombergClient(clock=clock).value("/isin/CH0012032048", "PX_LAST", date(2026, 10, 21))
    b = MockBloombergClient(clock=clock).value("/isin/CH0012032048", "PX_LAST", date(2026, 10, 21))
    assert a == b


def test_delivery_completes_order_and_fills_timeseries(client, service):
    order = subscribe(client, valid_isins(2), field_sets=["PRICING_EOD", "REFERENCE"]).json()
    assert order["status"] == "SUBMITTED"

    [result] = service.process_pending_deliveries()
    assert result["status"] == "COMPLETED"
    assert result["values"] == 6  # 2 Instrumente x 3 Felder
    assert client.get(f"/api/v1/orders/{order['id']}", headers=ALICE).json()["status"] == "COMPLETED"

    inst = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]
    assert inst["name"].startswith("Mock CH")
    ts = client.get(f"/api/v1/timeseries?instrumentId={inst['id']}&field=PX_LAST&from=2026-10-01&to=2026-10-31",
                    headers=ALICE).json()
    assert [p["date"] for p in ts["points"]] == ["2026-10-21"]

    # Bereits verarbeitete Antworten werden nicht erneut geholt.
    assert service.process_pending_deliveries() == []


def test_invalid_isin_is_rejected_and_not_billed(client, service):
    order = subscribe(client, valid_isins(1) + isins(1, start=5)).json()
    [result] = service.process_pending_deliveries()
    assert result["status"] == "COMPLETED"
    assert list(result["errors"].values()) == ["INVALID_SECURITY"]

    statuses = {i["identifiers"][0]["value"]: i["status"]
                for i in client.get("/api/v1/instruments", headers=ALICE).json()["items"]}
    assert statuses[isins(1, start=5)[0]["value"]] == "INACTIVE"
    usage = client.get("/api/v1/usage?month=2026-10", headers=ALICE).json()
    assert usage[0]["uniqueInstruments"] == 1
    assert order["estimate"]["additionalMonthlyCost"] == "3.00"


def test_order_fails_when_all_instruments_are_invalid(client, service):
    order = subscribe(client, isins(1, start=5)).json()
    service.process_pending_deliveries()
    assert client.get(f"/api/v1/orders/{order['id']}", headers=ALICE).json()["status"] == "FAILED"


def test_daily_delivery_records_corrections_bitemporally(client, service, clock, bloomberg):
    bloomberg.correction_rate = 1.0  # jede Tageslieferung korrigiert den Vortagespreis
    subscribe(client, valid_isins(1))
    service.process_pending_deliveries()
    inst_id = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]

    clock.now = datetime(2026, 10, 22, 18, 0)
    r = client.post("/api/v1/deliveries/run", headers=ADMIN)
    assert r.status_code == 200, r.text
    summary = r.json()
    assert summary["instruments"] == 1
    assert summary["corrections"] == 1  # PX_LAST vom 21.10.

    base = f"/api/v1/timeseries?instrumentId={inst_id}&field=PX_LAST&from=2026-10-21&to=2026-10-22"
    now = client.get(base, headers=ALICE).json()["points"]
    before = client.get(base + "&asOf=2026-10-22T12:00:00", headers=ALICE).json()["points"]
    assert [p["date"] for p in now] == ["2026-10-21", "2026-10-22"]
    assert len(before) == 1
    assert before[0]["value"] != now[0]["value"]


def test_daily_delivery_bills_again_in_new_month(client, service, clock):
    subscribe(client, valid_isins(1))
    service.process_pending_deliveries()
    clock.now = datetime(2026, 11, 2, 18, 0)
    client.post("/api/v1/deliveries/run", headers=ADMIN)
    usage = client.get("/api/v1/usage?month=2026-11", headers=ALICE).json()
    assert usage == [{"month": "2026-11", "category": "Pricing", "uniqueInstruments": 1,
                      "cost": "1.50", "currency": "CHF"}]


def test_daily_delivery_applies_due_unsubscriptions(client, clock, bloomberg):
    subscribe(client, valid_isins(1))
    order = client.post("/api/v1/orders", headers=ALICE,
                        json={"type": "UNSUBSCRIBE", "instruments": valid_isins(1)}).json()
    clock.now = datetime(2026, 11, 1, 18, 0)
    summary = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert len(summary["removed"]) == 1
    assert summary["instruments"] == 0
    assert bloomberg.requests[-1][1].remove == [f"/isin/{valid_isins(1)[0]['value']}"]
    assert client.get(f"/api/v1/orders/{order['id']}", headers=ALICE).json()["status"] == "COMPLETED"
    assert client.get("/api/v1/usage?month=2026-11", headers=ALICE).json() == []


def test_daily_delivery_needs_admin(client):
    assert client.post("/api/v1/deliveries/run", headers=ALICE).status_code == 403
