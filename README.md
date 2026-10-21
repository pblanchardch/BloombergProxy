# BloombergProxy

Lizenz-Service für die Bloomberg Data License: Instrumente bestellen und abbestellen,
Lizenzkosten optimieren, Zeitreihen bereitstellen – über ein Frontend und eine REST-API.

Anforderungskatalog: https://claude.ai/artifact/9GJaqQp9dmCtVSZANLy5cL

## Inhalt

| Pfad | Inhalt |
| --- | --- |
| `api/openapi.yaml` | API-Spezifikation (OpenAPI 3.1) |
| `db/ddl.sql` | Datenmodell für Databricks (Unity Catalog, Delta Lake) |
| `src/bloombergproxy/` | Lizenz-Service (FastAPI) mit Frontend unter `static/` |
| `tests/` | Tests (pytest) |

## Eckdaten

- Bloomberg Data License (ohne Data License Plus)
- Positionen aus Xentis, einmal pro Tag
- Zielplattform Databricks (Bronze, Silver, Gold)
- 4-Augen-Freigabe ab 200 CHF Mehrkosten pro Bestellung

## Lokal starten

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m bloombergproxy.devseed        # Beispiel-Feldlisten und Platzhalterpreise
uvicorn bloombergproxy.main:create_app --factory --reload
```

Frontend: http://localhost:8000 · API-Dokumentation: http://localhost:8000/docs · Tests: `pytest`

Lokal läuft der Service mit SQLite. Für Databricks `BBGPROXY_DATABASE_URL` auf eine
`databricks://`-URL setzen (`pip install -e ".[databricks]"`) und vorher `db/ddl.sql` ausführen.

## Stand Phase 1

- Bestellen und Abbestellen über Frontend und API (B-01, B-02, B-04, B-05)
- Kostenprognose je Kategorie; im Monat schon abgerechnete Kategorien kosten nichts (B2)
- Duplikate werden übersprungen (B3); Idempotency-Key gegen Doppelbestellungen
- 4-Augen-Freigabe ab 200 CHF oder wenn für eine Kategorie noch kein Preis hinterlegt ist
- Abbestellungen wirken zum Monatsende (`LicenseService.apply_due_unsubscriptions`, täglich aufrufen)
- Zeitreihen bitemporal mit Stichtagsabfrage, JSON oder CSV (B-06, C1)
- Feldkatalog, Feldlisten, Prüfpfad (D2, D3)

## Offen

- **Bloomberg-Adapter:** `RecordingBloombergClient` protokolliert Requests nur. Der Adapter für die
  Data License REST API folgt, sobald Zugangsdaten und Katalog-ID vorliegen.
- **Anmeldung:** Benutzer und Scopes kommen im Entwicklungsmodus aus den Headern `X-User` und
  `X-Scopes`. Vor dem Betrieb durch OAuth2 (z. B. Entra ID) ersetzen.
- **Preise:** Die Vertragswerte fehlen noch; die Preistabelle wird über `PUT /pricing/categories` gepflegt.
- **Phase 2:** Xentis-Abgleich (`/universe/reconcile`), Backfills, Datenqualität, Benachrichtigungen.
