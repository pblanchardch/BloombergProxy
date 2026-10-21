"""Tageslauf: Xentis-Abgleich, Lieferungen (C3), Datenqualität (D1)."""

from .quality import DataQuality
from .reconcile import SYSTEM_USER, Reconciler
from .service import Invalid


def daily_run(reconciler: Reconciler, quality: DataQuality, user: str) -> dict:
    """Im Betrieb täglich nach Börsenschluss aufrufen (z. B. als Databricks-Job)."""
    result = {}
    if reconciler.settings.xentis_auto_reconcile:
        try:
            result["reconcile"] = reconciler.reconcile(dry_run=False, user=SYSTEM_USER)
        except Invalid as e:
            result["reconcile"] = {"skipped": str(e)}
    result |= reconciler.svc.run_daily_delivery(user)
    if result.get("instruments") and result["date"].weekday() < 5:
        result["dataQuality"] = len(quality.check(result["date"]))
    return result
