import pytest

from bloombergproxy.esg import Esg
from conftest import ADMIN, ALICE, as_user, subscribe, valid_isins

ISIN = valid_isins(1)[0]["value"]


def setup(client, service):
    subscribe(client, [{"type": "ISIN", "value": ISIN}], field_sets=["PRICING_EOD", "ESG"])
    client.post(f"/api/v1/orders/{client.get('/api/v1/orders?status=PENDING_APPROVAL', headers=ALICE).json()['items'][0]['id']}/approve",
                headers=as_user("bob", "lizenz.read", "lizenz.approve"))
    service.process_pending_deliveries()
    return client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]


def vendor(client, name, value, day="21.10.2026"):
    r = client.post(f"/api/v1/vendors/{name}/values", headers=ADMIN | {"Content-Type": "text/csv"},
                    content=f"Kennungstyp;Kennung;Datum;Feld;Wert\nISIN;{ISIN};{day};ESG_SCORE;{value}\n".encode())
    assert r.json()["values"] == 1, r.text


@pytest.mark.parametrize("source,raw,expected", [
    ("BLOOMBERG", "7.5", 75.0), ("MSCI", "AAA", 100.0), ("MSCI", "ccc", 0.0), ("MSCI", "BBB", 50.0),
    ("SUSTAINALYTICS", "10", 80.0), ("SUSTAINALYTICS", "70", 0.0), ("UNBEKANNT", "5", None), ("MSCI", "X", None),
])
def test_normalization(service, source, raw, expected):
    assert Esg(service).normalize(source, raw) == expected


def test_consolidated_view_flags_divergence(client, service):
    inst = setup(client, service)
    vendor(client, "msci", "AA")
    vendor(client, "msci", "A", day="01.10.2026")  # älter, zählt nicht
    vendor(client, "sustainalytics", "40")
    [s] = client.get(f"/api/v1/esg?instrumentId={inst}", headers=ALICE).json()
    assert [x["source"] for x in s["scores"]] == ["BLOOMBERG", "MSCI", "SUSTAINALYTICS"]
    msci = s["scores"][1]
    assert (msci["raw"], msci["normalized"], msci["date"]) == ("AA", 83.3, "2026-10-21")
    assert s["scores"][2]["normalized"] == 20.0
    assert s["spread"] >= 63.3 and s["divergent"] is True
    assert client.get("/api/v1/esg", headers=ALICE).json()[0]["instrumentId"] == inst


def test_esg_respects_entitlements(client, service):
    setup(client, service)
    client.put("/api/v1/entitlements", headers=ADMIN, json=[{"category": "ESG", "requiredRole": "daten.esg"}])
    assert client.get("/api/v1/esg", headers=ALICE).status_code == 403
    assert client.get("/api/v1/esg", headers=as_user("e", "lizenz.read", "daten.esg")).status_code == 200
