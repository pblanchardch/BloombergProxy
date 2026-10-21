"""Anbindung an die Bloomberg Data License.

Der Service spricht Bloomberg nur über das Protokoll ``BloombergClient`` an. Ablauf wie bei
der Data License: Ein Request wird übermittelt, die Antwort steht später zum Abholen bereit.

``MockBloombergClient`` verhält sich wie die Data License, ruft Bloomberg aber nicht auf:
Er liefert deterministische Beispielwerte, meldet ungültige ISINs als Fehler und korrigiert
bei Tageslieferungen gelegentlich den Vortagespreis (PX_LAST). Der Adapter für die echte REST API folgt,
sobald Zugangsdaten und Katalog-ID vorliegen.
"""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class UniverseChange:
    """Instrumente (Bloomberg-Identifier wie ``/isin/CH0012032048``) und Felder."""

    add: list[str]
    remove: list[str]
    fields: list[str]


@dataclass(frozen=True)
class Record:
    identifier: str
    field: str
    value_date: date
    value: Decimal | str | None


@dataclass(frozen=True)
class Delivery:
    request_id: str
    records: list[Record]
    errors: dict[str, str]  # Identifier -> Fehlercode, z. B. INVALID_SECURITY


class BloombergClient(Protocol):
    def submit(self, change: UniverseChange) -> str:
        """Übermittelt eine Änderung am Universum und gibt die Request-ID zurück."""
        ...

    def request_snapshot(self, fields_by_identifier: dict[str, list[str]], as_of: date) -> str:
        """Fordert die Tageslieferung (C3) für das Universum an."""
        ...

    def request_history(self, identifiers: list[str], fields: list[str], start: date, end: date) -> str:
        """Fordert historische Werte (Backfill, C2) für Werktage von ``start`` bis ``end`` an."""
        ...

    def fetch(self, request_id: str) -> Delivery | None:
        """Holt die Antwort ab; ``None``, solange sie noch nicht bereit ist."""
        ...


def bbg_identifier(id_type: str, value: str) -> str:
    """Identifier im Format der Data License REST API, z. B. ``/isin/CH0012032048``."""
    return f"/{id_type.lower()}/{value}"


def parse_identifier(identifier: str) -> tuple[str, str]:
    _, id_type, value = identifier.split("/", 2)
    return id_type.upper(), value


def valid_isin(isin: str) -> bool:
    """Prüfziffer nach ISO 6166 (Luhn über die in Ziffern umgewandelten Zeichen)."""
    if len(isin) != 12 or not isin[:2].isalpha() or not isin.isalnum() or not isin[-1].isdigit():
        return False
    digits = "".join(str(int(c, 36)) for c in isin.upper()[:-1])
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d) * (2 if i % 2 == 0 else 1)
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(isin[-1])


def _unit(*parts: object) -> float:
    """Deterministische Zahl in [0, 1) aus den Teilen."""
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


@dataclass
class MockBloombergClient:
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    correction_rate: float = 0.1
    requests: list[tuple[str, UniverseChange]] = field(default_factory=list)
    # Request-ID -> (Felder je Identifier, Stichtage, Tageslieferung mit Korrekturen?)
    _pending: dict[str, tuple[dict[str, list[str]], list[date], bool]] = field(default_factory=dict)

    def _new(self, fields_by_identifier: dict[str, list[str]], dates: list[date], snapshot: bool) -> str:
        request_id = f"mock-{uuid.uuid4().hex[:12]}"
        self._pending[request_id] = (fields_by_identifier, dates, snapshot)
        return request_id

    def submit(self, change: UniverseChange) -> str:
        request_id = self._new({i: change.fields for i in change.add}, [self.clock().date()], False)
        self.requests.append((request_id, change))
        return request_id

    def request_snapshot(self, fields_by_identifier: dict[str, list[str]], as_of: date) -> str:
        return self._new(fields_by_identifier, [as_of], True)

    def request_history(self, identifiers: list[str], fields: list[str], start: date, end: date) -> str:
        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
        return self._new({i: fields for i in identifiers}, [d for d in days if d.weekday() < 5], False)

    def fetch(self, request_id: str) -> Delivery | None:
        if request_id not in self._pending:
            return None
        fields_by_identifier, dates, snapshot = self._pending.pop(request_id)
        records, errors = [], {}
        for identifier, fields in fields_by_identifier.items():
            id_type, value = parse_identifier(identifier)
            if id_type == "ISIN" and not valid_isin(value):
                errors[identifier] = "INVALID_SECURITY"
                continue
            for f in fields:
                records += [Record(identifier, f, d, self.value(identifier, f, d)) for d in dates]
            as_of = dates[-1]
            if snapshot and "PX_LAST" in fields and _unit("correction", identifier, as_of) < self.correction_rate:
                previous = as_of - timedelta(days=1)
                records.append(Record(identifier, "PX_LAST", previous,
                                      self.value(identifier, "PX_LAST", previous, corrected=True)))
        return Delivery(request_id, records, errors)

    @staticmethod
    def value(identifier: str, f: str, on: date, corrected: bool = False) -> Decimal | str | None:
        _, code = parse_identifier(identifier)
        if f == "PX_LAST":
            base = 10 + 490 * _unit(identifier)
            drift = 1 + 0.04 * (_unit(identifier, on) - 0.5) + (0.01 if corrected else 0)
            return Decimal(str(round(base * drift, 4)))
        if f == "PX_VOLUME":
            return Decimal(int(10_000 + 990_000 * _unit(identifier, f, on)))
        if f == "NAME":
            return f"Mock {code}"
        if f == "CRNCY":
            return "CHF" if code.startswith("CH") else "USD"
        if f == "ESG_SCORE":
            return Decimal(str(round(10 * _unit(identifier, f), 2)))
        return None
