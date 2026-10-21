"""Nutzungsrechte (B4).

Je Datenkategorie lässt sich festlegen,
* welche Rolle (Entra-App-Rolle, z. B. ``daten.esg``) die Daten lesen darf – ohne Eintrag
  dürfen alle mit ``lizenz.read`` lesen;
* ob die Daten weitergegeben werden dürfen – wenn nicht, ist der CSV-Export gesperrt.

Jeder Zugriff auf eine eingeschränkte Kategorie wird im Prüfpfad festgehalten
(DATA_ACCESSED); daraus entsteht der Nutzungsbericht für Lizenzprüfungen.
"""

from collections import defaultdict
from datetime import date

from sqlalchemy import Connection, delete, insert, select

from . import db
from .auth import User
from .models import ApiModel
from .service import Forbidden, LicenseService


class Entitlement(ApiModel):
    category: str
    required_role: str | None = None
    redistributable: bool = True
    note: str | None = None


class UsageByCategory(ApiModel):
    category: str
    users: list[str]
    accesses: int


class Entitlements:
    def __init__(self, service: LicenseService):
        self.svc = service

    def all(self) -> list[Entitlement]:
        with self.svc.engine.connect() as conn:
            rows = conn.execute(select(db.category_entitlement)
                                .order_by(db.category_entitlement.c.category)).mappings().all()
        return [Entitlement(**r) for r in rows]

    def replace(self, rules: list[Entitlement], user: str) -> None:
        with self.svc.engine.begin() as conn:
            conn.execute(delete(db.category_entitlement))
            if rules:
                conn.execute(insert(db.category_entitlement), [r.model_dump() for r in rules])
            self.svc._audit(conn, user, "ENTITLEMENTS_CHANGED", "entitlements", "categories",
                            [r.model_dump(by_alias=True) for r in rules])

    def _rule(self, conn: Connection, field: str) -> Entitlement | None:
        f, e = db.field_catalog.c, db.category_entitlement.c
        row = conn.execute(select(db.category_entitlement).join(
            db.field_catalog, f.category == e.category).where(f.mnemonic == field)).mappings().first()
        return Entitlement(**row) if row else None

    def allowed(self, user: User, rule: Entitlement | None) -> bool:
        return rule is None or rule.required_role is None or rule.required_role in user.scopes

    def check_read(self, user: User, instrument_id: str, field: str, export: bool) -> None:
        """Wirft Forbidden, wenn der Zugriff nicht erlaubt ist; protokolliert eingeschränkte Zugriffe."""
        denied = None
        with self.svc.engine.begin() as conn:
            rule = self._rule(conn, field)
            if rule is None:
                return
            details = {"field": field, "instrumentId": instrument_id, "export": export}
            if not self.allowed(user, rule):
                denied = f"Keine Berechtigung für Kategorie {rule.category} (Rolle {rule.required_role})"
            elif export and not rule.redistributable:
                denied = f"Kategorie {rule.category} ist nicht zur Weitergabe freigegeben; kein Export"
            # Auch Verweigerungen bleiben im Prüfpfad: erst speichern, dann ablehnen.
            self.svc._audit(conn, user.name, "DATA_ACCESS_DENIED" if denied else "DATA_ACCESSED",
                            "category", rule.category, details)
        if denied:
            raise Forbidden(denied)

    def usage(self, month: str) -> list[UsageByCategory]:
        """Wer hat im Monat auf eingeschränkte Kategorien zugegriffen?"""
        a = db.audit_log.c
        first = date.fromisoformat(f"{month}-01")
        nxt = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        with self.svc.engine.connect() as conn:
            rows = conn.execute(select(a.entity_id, a.user_name).where(
                a.action == "DATA_ACCESSED", a.at >= first, a.at < nxt)).all()
        users: dict[str, set[str]] = defaultdict(set)
        counts: dict[str, int] = defaultdict(int)
        for category, name in rows:
            users[category].add(name)
            counts[category] += 1
        return [UsageByCategory(category=c, users=sorted(users[c]), accesses=counts[c]) for c in sorted(users)]

    def annotate(self, user: User) -> dict[str, dict]:
        """Für den Feldkatalog: Regel und ob der aktuelle Benutzer lesen darf."""
        return {r.category: {"requiredRole": r.required_role, "redistributable": r.redistributable,
                             "allowed": self.allowed(user, r)} for r in self.all()}

