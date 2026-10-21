from datetime import date, datetime
from decimal import Decimal

from bloombergproxy.bloomberg import CorporateAction, MockBloombergClient
from bloombergproxy.corporate_actions import CorporateActions
from bloombergproxy.quality import DataQuality
from conftest import ADMIN, ALICE, subscribe, valid_isins

ISIN = valid_isins(1)[0]["value"]
CSV_HEAD = "Kennungstyp;Kennung;EreignisId;Art;Status;ExDatum;Zahltag;Betrag;Waehrung;Verhaeltnis\n"


def setup(client, service):
    subscribe(client, [{"type": "ISIN", "value": ISIN}])
    service.process_pending_deliveries()
    return client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]


def dividend(status="ANNOUNCED", amount="1.20", key="BBG-1"):
    return CorporateAction(f"/isin/{ISIN}", key, "DVD_CASH", status, date(2026, 11, 12),
                           pay_date=date(2026, 11, 15), amount=Decimal(amount), currency="CHF")


def store(service, actions, source="BLOOMBERG"):
    with service.engine.begin() as conn:
        return CorporateActions(service).store_all(conn, actions, source, "test", "test")


def golden(client, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/v1/corporate-actions?{q}", headers=ALICE).json()


def test_changes_are_kept_bitemporally(client, service):
    setup(client, service)
    assert store(service, [dividend()]) == {"new": 1, "changed": 0, "mismatches": 0}
    assert store(service, [dividend()]) == {"new": 0, "changed": 0, "mismatches": 0}
    assert store(service, [dividend(status="CONFIRMED", amount="1.25")])["changed"] == 1
    [g] = golden(client)
    assert (g["status"], g["amount"], g["sources"]) == ("CONFIRMED", "1.250000", ["BLOOMBERG"])


def test_golden_copy_merges_sources_and_flags_mismatch(client, service):
    inst = setup(client, service)
    store(service, [dividend()])
    r = client.post("/api/v1/vendors/refinitiv/corporate-actions", headers=ADMIN | {"Content-Type": "text/csv"},
                    content=(CSV_HEAD + f"ISIN;{ISIN};R-77;DVD_CASH;ANNOUNCED;12.11.2026;16.11.2026;1.20;CHF;\n"
                             + "ISIN;CH9999999999;R-78;DVD_CASH;ANNOUNCED;12.11.2026;;1;CHF;\n"
                             + f"ISIN;{ISIN};R-79;BONUS;ANNOUNCED;12.11.2026;;;;\n").encode()).json()
    assert (r["new"], r["mismatches"], len(r["skipped"])) == (1, 1, 2)
    [g] = golden(client, instrumentId=inst)
    assert g["sources"] == ["BLOOMBERG", "REFINITIV"]
    assert g["source"] == "BLOOMBERG" and g["payDate"] == "2026-11-15"
    assert g["mismatch"] == ["pay_date"]
    [exc] = client.get("/api/v1/data-quality/exceptions?status=OPEN", headers=ALICE).json()
    assert exc["type"] == "CA_MISMATCH" and "Zahltag" in exc["detail"]
    assert [a["source"] for a in golden(client, source="refinitiv")] == ["REFINITIV"]


def test_split_suppresses_outlier_and_notifies(client, service):
    inst = setup(client, service)
    store(service, [CorporateAction(f"/isin/{ISIN}", "S-1", "STOCK_SPLIT", "CONFIRMED", date(2026, 10, 21),
                                    ratio=Decimal(2))])
    with service.engine.begin() as conn:
        service.write_value(conn, inst, "PX_LAST", date(2026, 10, 20), Decimal("200"), "BLOOMBERG", "t")
        service.write_value(conn, inst, "PX_LAST", date(2026, 10, 21), Decimal("100"), "BLOOMBERG", "t")
    found = DataQuality(service).check(date(2026, 10, 21))
    assert "OUTLIER" not in [f["type"] for f in found]
    titles = [n["title"] for n in client.get("/api/v1/notifications", headers=ALICE).json()]
    assert "Corporate Action: Split confirmed" in titles


def test_mock_delivers_actions_in_daily_run(client, service, clock):
    isins = valid_isins(30)
    subscribe(client, isins)
    service.process_pending_deliveries()
    clock.now = datetime(2026, 10, 22, 18, 0)
    summary = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert summary["corporateActions"]["new"] > 0
    actions = golden(client, **{"from": "2026-10-15", "to": "2027-01-20"})
    assert {a["actionType"] for a in actions} <= {"DVD_CASH", "STOCK_SPLIT"}
    assert all(a["status"] == ("CONFIRMED" if a["exDate"] <= "2026-10-22" else "ANNOUNCED") for a in actions)
    # Ein zweiter Lauf am selben Tag bringt nichts Neues
    again = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert again["corporateActions"]["new"] == 0


def test_mock_is_deterministic(clock):
    a = MockBloombergClient(clock=clock)._actions(f"/isin/{ISIN}", date(2026, 1, 1), date(2026, 12, 31))
    b = MockBloombergClient(clock=clock)._actions(f"/isin/{ISIN}", date(2026, 1, 1), date(2026, 12, 31))
    assert a == b
