"""Benachrichtigungen (D4).

Meldungen werden in der Transaktion des Ereignisses gespeichert (app.notification) und erst
nach dem Commit verschickt (``flush``). So geht keine Meldung verloren, und es wird nichts
gemeldet, was nicht gespeichert wurde. Kanal: Webhook, z. B. ein Teams-Workflow
„Bei Empfang einer Webhookanforderung im Kanal posten“ (Format ``teams``) oder ``plain``.
"""

import json
import logging
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Connection, Engine, select, update

from . import db
from .config import Settings

log = logging.getLogger(__name__)

Transport = Callable[[str, dict], None]


def http_post(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as res:
        if res.status >= 300:
            raise RuntimeError(f"HTTP {res.status}")


class Notifier:
    def __init__(self, engine: Engine, settings: Settings, clock: Callable[[], datetime],
                 transport: Transport = http_post):
        self.engine = engine
        self.settings = settings
        self.clock = clock
        self.transport = transport

    def add(self, conn: Connection, event: str, title: str, text: str, details: dict | None = None) -> None:
        conn.execute(db.notification.insert().values(
            notification_id=str(uuid.uuid4()), created_at=self.clock(), event=event, title=title, text=text,
            details=json.dumps(details, default=str) if details else None, status="PENDING"))

    def flush(self) -> int:
        """Verschickt gespeicherte Meldungen; gibt die Anzahl erfolgreich versandter zurück."""
        n = db.notification.c
        with self.engine.begin() as conn:
            pending = conn.execute(select(db.notification).where(n.status == "PENDING")
                                   .order_by(n.created_at)).mappings().all()
            sent = 0
            for row in pending:
                status, error = "STORED", None  # ohne Kanal nur im Service sichtbar
                if self.settings.notify_webhook_url:
                    try:
                        self.transport(self.settings.notify_webhook_url, self._payload(row))
                        status, sent = "SENT", sent + 1
                    except Exception as e:  # noqa: BLE001 – Versandfehler dürfen den Service nicht stören
                        status, error = "FAILED", str(e)[:500]
                        log.warning("Benachrichtigung %s nicht versandt: %s", row["notification_id"], e)
                conn.execute(update(db.notification).where(n.notification_id == row["notification_id"])
                             .values(status=status, error=error, sent_at=self.clock()))
            return sent

    def _payload(self, row) -> dict:
        if self.settings.notify_webhook_format != "teams":
            return {"event": row["event"], "title": row["title"], "text": row["text"],
                    "details": json.loads(row["details"]) if row["details"] else None,
                    "createdAt": row["created_at"].isoformat()}
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {"type": "TextBlock", "text": row["title"], "weight": "Bolder", "size": "Medium", "wrap": True},
                {"type": "TextBlock", "text": row["text"], "wrap": True},
            ],
        }
        if self.settings.public_url:
            card["actions"] = [{"type": "Action.OpenUrl", "title": "Im Lizenz-Service öffnen",
                                "url": self.settings.public_url}]
        return {"type": "message", "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}

    def recent(self, limit: int = 100) -> list[dict]:
        n = db.notification.c
        with self.engine.connect() as conn:
            rows = conn.execute(select(db.notification).order_by(n.created_at.desc()).limit(limit)).mappings().all()
        return [{"id": r["notification_id"], "createdAt": r["created_at"], "event": r["event"], "title": r["title"],
                 "text": r["text"], "status": r["status"], "error": r["error"]} for r in rows]
