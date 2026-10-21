import json

from bloombergproxy.jobs import main


def test_cli_imports_new_files_once_and_runs_daily(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BBGPROXY_DATABASE_URL", f"sqlite:///{tmp_path / 'job.db'}")
    incoming = tmp_path / "xentis"
    incoming.mkdir()
    (incoming / "positionen_20261021.csv").write_text(
        "Stichtag;Portfolio;ISIN;Bestand\n21.10.2026;PF1;CH0012032048;100\n", encoding="utf-8")
    (incoming / "notizen.txt").write_text("keine CSV")

    assert main(["import-positions", str(incoming)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert [r["file"] for r in first] == ["positionen_20261021.csv"]

    assert main(["import-positions", str(incoming)]) == 0
    assert json.loads(capsys.readouterr().out) == []

    # Ohne Feldlisten im Katalog kann der Abgleich nicht bestellen; der Lauf meldet das und endet sauber.
    monkeypatch.setenv("BBGPROXY_XENTIS_AUTO_RECONCILE", "false")
    assert main(["daily"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["instruments"] == 0


def test_mock_state_is_shared_between_processes(tmp_path, clock):
    from datetime import date

    from bloombergproxy.bloomberg import MockBloombergClient, UniverseChange

    service_side = MockBloombergClient(clock=clock, state_dir=str(tmp_path))
    request_id = service_side.submit(UniverseChange(add=["/isin/CH0012032048"], remove=[], fields=["PX_LAST"]))
    ca_id = service_side.request_corporate_actions(["/isin/CH0012032048"], date(2026, 1, 1), date(2026, 12, 31))

    job_side = MockBloombergClient(clock=clock, state_dir=str(tmp_path))
    delivery = job_side.fetch(request_id)
    assert [r.field for r in delivery.records] == ["PX_LAST"]
    assert job_side.fetch(ca_id) is not None
    assert job_side.fetch(request_id) is None  # abgeholt
    assert list(tmp_path.iterdir()) == []
