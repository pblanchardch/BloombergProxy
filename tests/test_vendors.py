from datetime import datetime

from bloombergproxy.quality import DataQuality
from conftest import ADMIN, ALICE, subscribe, valid_isins

ISIN = valid_isins(1)[0]["value"]


def post(client, path, text):
    return client.post(path, headers=ADMIN | {"Content-Type": "text/csv"}, content=text.encode())


def setup(client, service):
    subscribe(client, [{"type": "ISIN", "value": ISIN}], field_sets=["PRICING_EOD", "REFERENCE_FULL"])
    service.process_pending_deliveries()
    return client.get("/api/v1/instruments", headers=ALICE).json()["items"][0]["id"]


def test_vendor_xref_and_values_via_own_identifier(client, service):
    inst = setup(client, service)
    r = post(client, "/api/v1/vendors/msci/xref", f"Kennungstyp;Kennung;ISIN\nMSCI_ID;IID000123;{ISIN}\n"
                                                  "MSCI_ID;IID999;CH9999999999\n").json()
    assert r["mapped"] == 1 and r["skipped"][0]["reason"] == "ISIN unbekannt"
    r = post(client, "/api/v1/vendors/msci/values",
             "Kennungstyp;Kennung;Datum;Feld;Wert\nMSCI_ID;IID000123;20.10.2026;PX_LAST;1'234.50\n"
             "MSCI_ID;IID000123;21.10.2026;PX_LAST;99\nMSCI_ID;UNBEKANNT;21.10.2026;PX_LAST;1\n").json()
    assert (r["values"], len(r["skipped"])) == (2, 1)
    assert client.get(f"/api/v1/instruments/{inst}/sources", headers=ALICE).json() == ["BLOOMBERG", "MSCI"]

    base = f"/api/v1/timeseries?instrumentId={inst}&field=PX_LAST&from=2026-10-20&to=2026-10-21"
    golden = client.get(base, headers=ALICE).json()
    # 20.10. nur MSCI, 21.10. Bloomberg hat Vorrang
    assert [(p["date"], p["source"]) for p in golden["points"]] == [("2026-10-20", "MSCI"), ("2026-10-21", "BLOOMBERG")]
    assert golden["points"][0]["value"] == "1234.5000000000"
    only = client.get(base + "&source=msci", headers=ALICE).json()
    assert [p["source"] for p in only["points"]] == ["MSCI", "MSCI"]


def test_precedence_is_configurable(client, service):
    inst = setup(client, service)
    post(client, "/api/v1/vendors/refinitiv/values",
         f"Kennungstyp;Kennung;Datum;Feld;Wert\nISIN;{ISIN};21.10.2026;PX_LAST;42\n")
    service.settings.source_precedence = ["REFINITIV", "BLOOMBERG"]
    ts = client.get(f"/api/v1/timeseries?instrumentId={inst}&field=PX_LAST&from=2026-10-21&to=2026-10-21",
                    headers=ALICE).json()
    assert ts["points"][0]["source"] == "REFINITIV"


def test_source_mismatch_is_a_quality_exception(client, service):
    inst = setup(client, service)
    post(client, "/api/v1/vendors/refinitiv/values",
         f"Kennungstyp;Kennung;Datum;Feld;Wert\nISIN;{ISIN};21.10.2026;PX_LAST;1\n")
    found = DataQuality(service).check(datetime(2026, 10, 21).date())
    assert [f["type"] for f in found] == ["SOURCE_MISMATCH"]
    assert "REFINITIV 1" in found[0]["detail"]


def test_vendor_name_and_admin_rules(client):
    assert post(client, "/api/v1/vendors/bloomberg/values", "x").status_code == 400
    assert client.post("/api/v1/vendors/msci/values", headers=ALICE, content=b"x").status_code == 403
    r = post(client, "/api/v1/vendors/msci/values", "Datum;Wert\n")
    assert r.status_code == 400 and "Kennungstyp" in r.json()["message"]
