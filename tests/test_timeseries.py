from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import insert

from bloombergproxy import db
from conftest import ALICE


def test_bitemporal_query(client, service):
    with service.engine.begin() as conn:
        conn.execute(insert(db.timeseries_value), [
            # Ursprünglicher Wert, am 2.10. korrigiert
            {"instrument_id": "i1", "field": "PX_LAST", "value_date": date(2026, 10, 1),
             "value_num": Decimal("100"), "recorded_at": datetime(2026, 10, 1, 18),
             "superseded_at": datetime(2026, 10, 2, 9), "bbg_request_id": "r1"},
            {"instrument_id": "i1", "field": "PX_LAST", "value_date": date(2026, 10, 1),
             "value_num": Decimal("101"), "recorded_at": datetime(2026, 10, 2, 9), "superseded_at": None,
             "bbg_request_id": "r2"},
            {"instrument_id": "i1", "field": "PX_LAST", "value_date": date(2026, 10, 2),
             "value_num": Decimal("102"), "recorded_at": datetime(2026, 10, 2, 18), "superseded_at": None,
             "bbg_request_id": "r2"},
        ])

    base = "/api/v1/timeseries?instrumentId=i1&field=PX_LAST&from=2026-10-01&to=2026-10-31"
    current = client.get(base, headers=ALICE).json()
    assert [(p["date"], p["value"]) for p in current["points"]] == [
        ("2026-10-01", "101.0000000000"), ("2026-10-02", "102.0000000000")]

    then = client.get(base + "&asOf=2026-10-01T20:00:00", headers=ALICE).json()
    assert [(p["date"], p["value"]) for p in then["points"]] == [("2026-10-01", "100.0000000000")]

    csv = client.get(base + "&format=csv", headers=ALICE)
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0] == "date,value,recordedAt,source"
