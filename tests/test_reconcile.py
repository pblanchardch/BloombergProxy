from datetime import datetime

from conftest import ADMIN, ALICE, BOB_APPROVER, subscribe, valid_isins

ISINS = [i["value"] for i in valid_isins(10)]


def positions_csv(rows, day="21.10.2026") -> str:
    lines = ["Stichtag;Portfolio;ISIN;Bestand"]
    lines += [f"{day};{pf};{isin};{qty}" for pf, isin, qty in rows]
    return "\n".join(lines) + "\n"


def upload(client, text, name="positions.csv"):
    return client.post(f"/api/v1/positions/import?fileName={name}", headers=ADMIN | {"Content-Type": "text/csv"},
                       content=text.encode("utf-8"))


def reconcile(client, dry_run=False):
    r = client.post("/api/v1/universe/reconcile", headers=ADMIN, json={"dryRun": dry_run})
    assert r.status_code == 202, r.text
    return r.json()


def universe(client):
    return {i["identifiers"][0]["value"]: i for i in client.get("/api/v1/instruments", headers=ALICE).json()["items"]}


def test_import_parses_swiss_formats_and_skips_rows_without_isin(client):
    text = positions_csv([("PF1", ISINS[0], "1'250.50"), ("PF1", "", "100"), ("PF2", ISINS[1], "-30")])
    r = upload(client, "﻿" + text)
    assert r.status_code == 200, r.text
    assert r.json() == {"asOfDates": ["2026-10-21"], "rows": 2, "skipped": [{"line": 3, "reason": "ohne ISIN"}]}


def test_import_rejects_missing_columns(client):
    r = upload(client, "Datum;ISIN\n21.10.2026;" + ISINS[0])
    assert r.status_code == 400
    assert "Stichtag" in r.json()["message"]


def test_import_needs_admin(client):
    r = client.post("/api/v1/positions/import", headers=ALICE, content=b"x")
    assert r.status_code == 403


def test_dry_run_proposes_without_ordering(client):
    upload(client, positions_csv([("PF1", ISINS[0], "100"), ("PF2", ISINS[1], "5")]))
    result = reconcile(client, dry_run=True)
    assert result["subscribe"] == 2
    assert result["orders"] == {}
    assert universe(client) == {}

    job = client.get(f"/api/v1/jobs/{result['jobId']}", headers=ALICE).json()
    assert job["kind"] == "RECONCILE"
    assert {p["isin"] for p in job["proposals"]} == {ISINS[0], ISINS[1]}


def test_new_positions_are_subscribed_with_source_xentis(client, bloomberg):
    upload(client, positions_csv([("PF1", ISINS[0], "100"), ("PF2", ISINS[0], "50"), ("PF2", ISINS[1], "5")]))
    result = reconcile(client)
    order = client.get(f"/api/v1/orders/{result['orders']['SUBSCRIBE']['id']}", headers=ALICE).json()
    assert order["source"] == "XENTIS"
    assert order["requestedBy"] == "xentis-abgleich"
    assert order["fieldSets"] == ["PRICING_EOD"]
    assert order["status"] == "SUBMITTED"
    assert {i["source"] for i in universe(client).values()} == {"XENTIS"}

    # Zweiter Lauf mit derselben Datei ändert nichts.
    again = reconcile(client)
    assert (again["subscribe"], again["unsubscribe"], again["orders"]) == (0, 0, {})


def test_netted_zero_position_is_not_held(client):
    upload(client, positions_csv([("PF1", ISINS[0], "100"), ("PF2", ISINS[0], "-100")]))
    assert reconcile(client, dry_run=True)["subscribe"] == 0


def test_closed_position_is_unsubscribed_at_month_end_manual_ones_untouched(client):
    subscribe(client, [{"type": "ISIN", "value": ISINS[9]}])  # manuell bestellt, nicht in Xentis
    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:6]]))
    reconcile(client)

    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:5]], day="22.10.2026"))
    result = reconcile(client)
    assert result["unsubscribe"] == 1
    assert result["unsubscribeBlocked"] is False
    u = universe(client)
    assert u[ISINS[5]]["status"] == "PENDING_UNSUBSCRIBE"
    assert u[ISINS[5]]["unsubscribeEffective"] == "2026-10-31"
    assert u[ISINS[9]]["status"] == "ACTIVE"


def test_safeguard_blocks_mass_unsubscribe(client):
    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:5]]))
    reconcile(client)
    # Unvollständige Datei: nur noch ein Instrument
    upload(client, positions_csv([("PF1", ISINS[0], "1")], day="22.10.2026"))
    result = reconcile(client)
    assert result["unsubscribe"] == 4
    assert result["unsubscribeBlocked"] is True
    assert "UNSUBSCRIBE" not in result["orders"]
    assert {i["status"] for i in universe(client).values()} == {"ACTIVE"}
    assert client.get(f"/api/v1/jobs/{result['jobId']}", headers=ALICE).json()["status"] == "FAILED"


def test_reopened_position_cancels_unsubscribe_without_cost(client):
    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:6]]))
    reconcile(client)
    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:5]], day="22.10.2026"))
    reconcile(client)
    upload(client, positions_csv([("PF1", isin, "1") for isin in ISINS[:6]], day="23.10.2026"))
    result = reconcile(client)
    order = client.get(f"/api/v1/orders/{result['orders']['SUBSCRIBE']['id']}", headers=ALICE).json()
    assert order["estimate"]["additionalMonthlyCost"] == "0"
    assert universe(client)[ISINS[5]]["status"] == "ACTIVE"


def test_large_xentis_order_waits_for_human_approval(client, service):
    service.settings.approval_threshold = 2  # 2 x 1.50 CHF reicht
    upload(client, positions_csv([("PF1", ISINS[0], "1"), ("PF1", ISINS[1], "1")]))
    result = reconcile(client)
    order_id = result["orders"]["SUBSCRIBE"]["id"]
    assert result["orders"]["SUBSCRIBE"]["status"] == "PENDING_APPROVAL"
    approved = client.post(f"/api/v1/orders/{order_id}/approve", headers=BOB_APPROVER).json()
    assert approved["status"] == "SUBMITTED"


def test_daily_run_reconciles_then_delivers(client, clock):
    upload(client, positions_csv([("PF1", ISINS[0], "1")]))
    clock.now = datetime(2026, 10, 21, 18, 0)
    summary = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert summary["reconcile"]["subscribe"] == 1
    assert [o["status"] for o in summary["orders"]] == ["COMPLETED"]
    assert summary["instruments"] == 1


def test_daily_run_without_positions_skips_reconcile(client):
    summary = client.post("/api/v1/deliveries/run", headers=ADMIN).json()
    assert "noch keine Xentis-Positionen" in summary["reconcile"]["skipped"]
