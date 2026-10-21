from datetime import datetime

import pytest

from conftest import ADMIN, ALICE, BOB_APPROVER, isins, subscribe, valid_isins


@pytest.fixture
def sent(service):
    calls = []
    service.settings.notify_webhook_url = "https://example.invalid/webhook"
    service.settings.public_url = "https://lizenz.example.ch"
    service.notifier.transport = lambda url, payload: calls.append(payload)
    return calls


def titles(calls):
    return [c["attachments"][0]["content"]["body"][0]["text"] for c in calls]


def test_pending_approval_sends_teams_card_after_request(client, sent):
    subscribe(client, isins(1), field_sets=["ESG"])
    assert titles(sent) == ["Bestellung wartet auf Freigabe"]
    card = sent[0]["attachments"][0]["content"]
    assert "ohne Preis: ESG" in card["body"][1]["text"]
    assert card["actions"][0]["url"] == "https://lizenz.example.ch"
    status = client.get("/api/v1/notifications", headers=ALICE).json()[0]["status"]
    assert status == "SENT"


def test_rejection_and_small_orders(client, sent):
    subscribe(client, isins(1))  # klein: keine Meldung
    order = subscribe(client, isins(1, start=3), field_sets=["ESG"]).json()
    client.post(f"/api/v1/orders/{order['id']}/reject", headers=BOB_APPROVER, json={"comment": "nicht nötig"})
    assert titles(sent) == ["Bestellung wartet auf Freigabe", "Bestellung abgelehnt"]


def test_delivery_errors_and_dq_reported_in_daily_run(client, sent, clock):
    subscribe(client, isins(1, start=5))  # ungültige Prüfziffer
    clock.now = datetime(2026, 10, 22, 18, 0)
    client.post("/api/v1/deliveries/run", headers=ADMIN)
    assert titles(sent) == ["Bestellung fehlgeschlagen"]


def test_daily_run_failure_is_reported(client, sent, bloomberg, monkeypatch):
    subscribe(client, valid_isins(1))

    def broken(*args):
        raise ConnectionError("Bloomberg nicht erreichbar")
    monkeypatch.setattr(bloomberg, "request_snapshot", broken)
    with pytest.raises(ConnectionError):
        client.post("/api/v1/deliveries/run", headers=ADMIN)
    assert titles(sent)[-1] == "Tageslauf fehlgeschlagen"


def test_failed_webhook_is_recorded_not_raised(client, service):
    service.settings.notify_webhook_url = "https://example.invalid/webhook"

    def down(url, payload):
        raise TimeoutError("Zeitüberschreitung")
    service.notifier.transport = down
    r = subscribe(client, isins(1), field_sets=["ESG"])
    assert r.status_code == 201
    [n] = client.get("/api/v1/notifications", headers=ALICE).json()
    assert (n["status"], n["error"]) == ("FAILED", "Zeitüberschreitung")


def test_without_webhook_notifications_are_only_stored(client):
    subscribe(client, isins(1), field_sets=["ESG"])
    [n] = client.get("/api/v1/notifications", headers=ALICE).json()
    assert n["status"] == "STORED"


def test_plain_format(client, service, sent):
    service.settings.notify_webhook_format = "plain"
    subscribe(client, isins(1), field_sets=["ESG"])
    assert sent[0]["event"] == "ORDER_PENDING_APPROVAL"
    assert "orderId" in sent[0]["details"]
