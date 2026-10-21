"""Tageslauf: Xentis-Abgleich, Lieferungen (C3), Datenqualität (D1), Benachrichtigungen (D4)."""

from .corporate_actions import CorporateActions
from .quality import DataQuality
from .reconcile import SYSTEM_USER, Reconciler
from .service import Invalid


def daily_run(reconciler: Reconciler, quality: DataQuality, user: str,
              actions: CorporateActions | None = None) -> dict:
    """Im Betrieb täglich nach Börsenschluss aufrufen (z. B. als Databricks-Job)."""
    svc = reconciler.svc
    try:
        result = {}
        if reconciler.settings.xentis_auto_reconcile:
            try:
                result["reconcile"] = reconciler.reconcile(dry_run=False, user=SYSTEM_USER)
            except Invalid as e:
                result["reconcile"] = {"skipped": str(e)}
        result |= svc.run_daily_delivery(user)
        # Corporate Actions vor der Datenqualität: ein Split erklärt einen Preissprung.
        result["corporateActions"] = (actions or CorporateActions(svc)).fetch_from_bloomberg(user)
        if result.get("instruments") and result["date"].weekday() < 5:
            found = quality.check(result["date"])
            result["dataQuality"] = len(found)
            if found:
                with svc.engine.begin() as conn:
                    svc.notifier.add(conn, "DQ_EXCEPTIONS", f"Datenqualität: {len(found)} neue Ausnahme(n)",
                                     f"Lieferung vom {result['date']:%d.%m.%Y}: "
                                     + ", ".join(sorted({x["type"] for x in found})),
                                     {"date": result["date"]})
        return result
    except Exception as e:
        with svc.engine.begin() as conn:
            svc.notifier.add(conn, "DAILY_RUN_FAILED", "Tageslauf fehlgeschlagen", f"{type(e).__name__}: {e}")
        raise
    finally:
        svc.notifier.flush()


def import_directory(reconciler: Reconciler, directory: str, user: str = SYSTEM_USER) -> list[dict]:
    """Importiert alle noch nicht importierten Xentis-Dateien (*.csv) eines Verzeichnisses,
    z. B. eines Unity-Catalog-Volumes. Bereits importierte Dateinamen werden übersprungen."""
    from pathlib import Path

    from sqlalchemy import select

    from . import db

    with reconciler.svc.engine.connect() as conn:
        done = set(conn.execute(select(db.xentis_positions_raw.c.file_name).distinct()).scalars())
    results = []
    for path in sorted(Path(directory).glob("*.csv")):
        if path.name in done:
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
        results.append({"file": path.name, **reconciler.import_positions(text, path.name, user)})
    return results


def main(argv: list[str] | None = None) -> int:
    """Einstieg für zeitgesteuerte Läufe, z. B. als Databricks-Job:

    bloombergproxy-job import-positions /Volumes/bbg_license/bronze/xentis
    bloombergproxy-job daily

    Einstellungen kommen wie beim Service aus den Umgebungsvariablen BBGPROXY_*.
    Der Bloomberg-Mock bleibt aktiv, bis der echte Adapter angebunden ist.
    """
    import argparse
    import json
    import logging

    from .bloomberg import MockBloombergClient
    from .config import Settings
    from .db import make_engine
    from .service import LicenseService

    parser = argparse.ArgumentParser(prog="bloombergproxy-job")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daily", help="Tageslauf: Xentis-Abgleich, Lieferungen, Corporate Actions, Datenqualität")
    imp = sub.add_parser("import-positions", help="Neue Xentis-Dateien aus einem Verzeichnis importieren")
    imp.add_argument("directory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    svc = LicenseService(make_engine(settings.database_url), MockBloombergClient(state_dir=settings.bloomberg_mock_state_dir or None), settings)
    reconciler = Reconciler(svc)
    if args.command == "daily":
        result = daily_run(reconciler, DataQuality(svc), SYSTEM_USER)
    else:
        result = import_directory(reconciler, args.directory)
        svc.notifier.flush()
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
