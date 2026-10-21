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
