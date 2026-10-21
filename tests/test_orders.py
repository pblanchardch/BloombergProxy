from datetime import datetime

from conftest import ADMIN, ALICE, BOB_APPROVER, as_user, isins, subscribe


def test_small_order_is_submitted_without_approval(client, bloomberg):
    r = subscribe(client, isins(2))
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "SUBMITTED"
    assert order["requiresApproval"] is False
    assert order["estimate"]["additionalMonthlyCost"] == "3.00"

    [(_, change)] = bloomberg.requests
    assert change.add == ["/isin/CH0000000000", "/isin/CH0000000001"]
    assert change.fields == ["PX_LAST", "PX_VOLUME"]

    universe = client.get("/api/v1/instruments", headers=ALICE).json()
    assert universe["total"] == 2
    assert {i["status"] for i in universe["items"]} == {"ACTIVE"}
    assert universe["items"][0]["categories"] == ["Pricing"]


def test_duplicate_order_is_refused(client, bloomberg):
    subscribe(client, isins(1))
    r = subscribe(client, isins(1))
    assert r.status_code == 400
    assert len(bloomberg.requests) == 1


def test_duplicates_within_order_are_skipped(client, bloomberg):
    r = subscribe(client, isins(1) + isins(1))
    est = r.json()["estimate"]
    assert est["duplicates"] == isins(1)
    assert est["additionalMonthlyCost"] == "1.50"
    assert bloomberg.requests[0][1].add == ["/isin/CH0000000000"]


def test_category_already_billed_this_month_costs_nothing(client):
    subscribe(client, isins(1), field_sets=["PRICING_EOD"])
    est = client.post("/api/v1/orders/estimate", headers=ALICE, json={
        "type": "SUBSCRIBE", "instruments": isins(1), "fieldSets": ["PRICING_VOL"]}).json()
    assert est["additionalMonthlyCost"] == "0"
    assert est["alreadyBilledInstruments"] == 1
    assert est["duplicates"] == []


def test_estimate_per_category(client):
    est = client.post("/api/v1/orders/estimate", headers=ALICE, json={
        "type": "SUBSCRIBE", "instruments": isins(10), "fieldSets": ["PRICING_EOD", "REFERENCE"]}).json()
    assert est["byCategory"] == [
        {"category": "Pricing", "newUniqueInstruments": 10, "cost": "15.00"},
        {"category": "SecurityMaster", "newUniqueInstruments": 10, "cost": "8.00"},
    ]
    assert est["additionalMonthlyCost"] == "23.00"


def test_order_from_200_chf_needs_second_person(client, bloomberg):
    order = subscribe(client, isins(134)).json()  # 134 x 1.50 = 201 CHF
    assert order["status"] == "PENDING_APPROVAL"
    assert bloomberg.requests == []

    # Bestellende Person darf nicht selbst freigeben, auch nicht mit Freigabe-Scope.
    self_approve = client.post(f"/api/v1/orders/{order['id']}/approve",
                               headers=as_user("alice", "lizenz.read", "lizenz.approve"))
    assert self_approve.status_code == 403

    no_scope = client.post(f"/api/v1/orders/{order['id']}/approve", headers=as_user("carol"))
    assert no_scope.status_code == 403

    approved = client.post(f"/api/v1/orders/{order['id']}/approve", headers=BOB_APPROVER,
                           json={"comment": "ok"}).json()
    assert approved["status"] == "SUBMITTED"
    assert approved["approvedBy"] == "bob"
    assert len(bloomberg.requests[0][1].add) == 134

    again = client.post(f"/api/v1/orders/{order['id']}/approve", headers=BOB_APPROVER)
    assert again.status_code == 409


def test_just_below_threshold_needs_no_approval(client):
    order = subscribe(client, isins(133)).json()  # 199.50 CHF
    assert order["status"] == "SUBMITTED"


def test_unpriced_category_needs_approval(client):
    order = subscribe(client, isins(1), field_sets=["ESG"]).json()
    assert order["estimate"]["unpricedCategories"] == ["ESG"]
    assert order["status"] == "PENDING_APPROVAL"


def test_reject_needs_comment_and_submits_nothing(client, bloomberg):
    order = subscribe(client, isins(1), field_sets=["ESG"]).json()
    assert client.post(f"/api/v1/orders/{order['id']}/reject", headers=BOB_APPROVER, json={}).status_code == 400
    rejected = client.post(f"/api/v1/orders/{order['id']}/reject", headers=BOB_APPROVER,
                           json={"comment": "ESG erst in Phase 3"}).json()
    assert rejected["status"] == "REJECTED"
    assert rejected["decisionComment"] == "ESG erst in Phase 3"
    assert bloomberg.requests == []


def test_unknown_field_set_is_rejected(client):
    r = subscribe(client, isins(1), field_sets=["NOPE"])
    assert r.status_code == 400
    assert "NOPE" in r.json()["message"]


def test_idempotency_key_returns_same_order(client, bloomberg):
    headers = ALICE | {"Idempotency-Key": "abc"}
    first = subscribe(client, isins(1), headers=headers).json()
    second = subscribe(client, isins(1), headers=headers).json()
    assert first["id"] == second["id"]
    assert len(bloomberg.requests) == 1


def test_missing_user_is_unauthorized(client):
    assert client.get("/api/v1/instruments").status_code == 401


def test_unsubscribe_takes_effect_at_month_end(client, service, clock, bloomberg):
    subscribe(client, isins(1))
    r = client.post("/api/v1/orders", headers=ALICE, json={"type": "UNSUBSCRIBE", "instruments": isins(1)})
    assert r.status_code == 201, r.text
    assert r.json()["effectiveDate"] == "2026-10-31"

    inst = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]
    assert inst["status"] == "PENDING_UNSUBSCRIBE"
    assert service.apply_due_unsubscriptions() == []

    clock.now = datetime(2026, 11, 1, 6, 0)
    assert service.apply_due_unsubscriptions() == [inst["id"]]
    assert bloomberg.requests[-1][1].remove == ["/isin/CH0000000000"]
    assert client.get(f"/api/v1/instruments/{inst['id']}", headers=ALICE).json()["status"] == "INACTIVE"


def test_resubscribe_in_same_month_is_free_and_cancels_unsubscribe(client):
    subscribe(client, isins(1))
    client.post("/api/v1/orders", headers=ALICE, json={"type": "UNSUBSCRIBE", "instruments": isins(1)})
    order = subscribe(client, isins(1)).json()
    assert order["estimate"]["additionalMonthlyCost"] == "0"
    inst = client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]
    assert inst["status"] == "ACTIVE"
    assert inst["unsubscribeEffective"] is None


def test_unsubscribe_unknown_instrument(client):
    r = client.post("/api/v1/orders", headers=ALICE, json={"type": "UNSUBSCRIBE", "instruments": isins(1)})
    assert r.status_code == 400


def test_usage_and_audit(client):
    subscribe(client, isins(3), field_sets=["PRICING_EOD", "REFERENCE"])
    usage = client.get("/api/v1/usage?month=2026-10", headers=ALICE).json()
    assert usage == [
        {"month": "2026-10", "category": "Pricing", "uniqueInstruments": 3, "cost": "4.50", "currency": "CHF"},
        {"month": "2026-10", "category": "SecurityMaster", "uniqueInstruments": 3, "cost": "2.40", "currency": "CHF"},
    ]
    actions = [a["action"] for a in client.get("/api/v1/audit", headers=ALICE).json()]
    assert set(actions) == {"ORDER_CREATED", "ORDER_SUBMITTED"}


def test_pricing_update_keeps_history(client):
    assert client.put("/api/v1/pricing/categories", headers=ALICE, json=[]).status_code == 403
    r = client.put("/api/v1/pricing/categories", headers=ADMIN, json=[
        {"category": "Pricing", "pricePerInstrument": "2.00", "currency": "CHF", "validFrom": "2026-11-01"}])
    assert r.status_code == 200
    current = {p["category"]: p["pricePerInstrument"] for p in
               client.get("/api/v1/pricing/categories", headers=ALICE).json()}
    assert current["Pricing"] == "2.00"
    changed = [a for a in client.get("/api/v1/audit", headers=ALICE).json() if a["action"] == "PRICING_CHANGED"]
    assert changed[0]["details"][0]["category"] == "Pricing"
    # Oktober wird weiterhin mit dem alten Preis gerechnet.
    subscribe(client, isins(1))
    assert client.get("/api/v1/usage?month=2026-10", headers=ALICE).json()[0]["cost"] == "1.50"


def test_field_catalog_and_sets(client):
    fields = client.get("/api/v1/fields?search=px", headers=ALICE).json()
    assert [f["mnemonic"] for f in fields] == ["PX_LAST", "PX_VOLUME"]
    assert fields[0]["pricePerInstrument"] == "1.50"
    sets = {s["id"]: s for s in client.get("/api/v1/field-sets", headers=ALICE).json()}
    assert sets["PRICING_EOD"]["categories"] == ["Pricing"]


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Bloomberg Lizenz" in r.text
