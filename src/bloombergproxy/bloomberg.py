"""Anbindung an die Bloomberg Data License.

Der Service spricht Bloomberg nur über das Protokoll ``BloombergClient`` an. Der echte
Adapter für die Data License REST API folgt, sobald Zugangsdaten und Katalog-ID vorliegen;
bis dahin protokolliert ``RecordingBloombergClient`` die Anfragen, ohne Bloomberg aufzurufen.
"""

import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class UniverseChange:
    """Instrumente (Bloomberg-Identifier wie ``/isin/CH0012032048``) und Felder."""

    add: list[str]
    remove: list[str]
    fields: list[str]


class BloombergClient(Protocol):
    def submit(self, change: UniverseChange) -> str:
        """Übermittelt die Änderung am Universum und gibt die Request-ID zurück."""
        ...


@dataclass
class RecordingBloombergClient:
    requests: list[tuple[str, UniverseChange]] = field(default_factory=list)

    def submit(self, change: UniverseChange) -> str:
        request_id = f"rec-{uuid.uuid4().hex[:12]}"
        self.requests.append((request_id, change))
        return request_id


def bbg_identifier(id_type: str, value: str) -> str:
    """Identifier im Format der Data License REST API, z. B. ``/isin/CH0012032048``."""
    if id_type == "TICKER":
        return f"/ticker/{value}"
    return f"/{id_type.lower()}/{value}"
