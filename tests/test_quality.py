from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert

from bloombergproxy import db
from bloombergproxy.quality import DataQuality
from conftest import ADMIN, ALICE, subscribe, valid_isins


def setup_instrument(client, service):
    subscribe(client, valid_isins(1))
    service.process_pending_deliveries()
    return client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]


def put_prices(service, instrument_id, prices: dict[date, str], field="PX_LAST"):
    t = db.timeseries_value.c
    with service.engine.begin() as conn:
        conn.execute(db.timeseries_value.delete().where(t.instrument_id == instrument_id, t.field == field))
        if not prices:
            return
        conn.execute(insert(db.timeseries_value), [
            {"instrument_id": instrument_id, "field": field, "value_date": d, "value_num": Decimal(v),
             "value_str": None, "recorded_at": datetime(2026, 10, 21, 18), "superseded_at": None,
             "bbg_request_id": "t"} for d, v in prices.items()])


DAYS = [date(2026, 10, 19) + timedelta(days=n) for n in range(3)]  # Mo-Mi


def test_outlier_detected_once(client, service):
    inst = setup_instrument(client, service)
    put_prices(service, inst, {DAYS[1]: "100", DAYS[2]: "130"})
    put_prices(service, inst, {DAYS[2]: "1000"}, field="PX_VOLUME")
    dq = DataQuality(service)
    found = dq.check(DAYS[2])
    assert [(x["type"], x["detail"]) for x in found] == [("OUTLIER", "100 -> 130 (+30.0%)")]
    assert dq.check(DAYS[2]) == []


def test_stale_and_missing_detected(client, service):
    inst = setup_instrument(client, service)
    put_prices(service, inst, {d: "50" for d in DAYS})
    put_prices(service, inst, {}, field="PX_VOLUME")
    found = DataQuality(service).check(DAYS[2])
    assert sorted((x["type"], x["field"]) for x in found) == [("MISSING_VALUE", "PX_VOLUME"),
                                                             ("STALE_PRICE", "PX_LAST")]


def test_exception_workflow_via_api(client, service):
    inst = setup_instrument(client, service)
    put_prices(service, inst, {DAYS[1]: "100", DAYS[2]: "50"})
    put_prices(service, inst, {DAYS[2]: "1000"}, field="PX_VOLUME")
    DataQuality(service).check(DAYS[2])

    [exc] = client.get("/api/v1/data-quality/exceptions?status=OPEN", headers=ALICE).json()
    assert exc["type"] == "OUTLIER"
    r = client.post(f"/api/v1/data-quality/exceptions/{exc['id']}/status", headers=ALICE,
                    json={"status": "RESOLVED", "comment": "Split 2:1, Wert korrekt"})
    assert r.json()["status"] == "RESOLVED"
    assert r.json()["handledBy"] == "alice"
    assert client.get("/api/v1/data-quality/exceptions?status=OPEN", headers=ALICE).json() == []
    bad = client.post(f"/api/v1/data-quality/exceptions/{exc['id']}/status", headers=ALICE, json={"status": "X"})
    assert bad.status_code == 400
    actions = [a["action"] for a in client.get("/api/v1/audit", headers=ALICE).json()]
    assert "DQ_RESOLVED" in actions


def test_daily_run_checks_quality_and_mock_data_is_clean(client, service, clock):
    setup_instrument(client, service)
    clock.now = datetime(2026, 10, 22, 18, 0)
    summary = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert summary["dataQuality"] == 0
