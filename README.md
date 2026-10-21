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
export BBGPROXY_AUTH_MODE=header        # Entwicklung ohne Entra ID
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
- Abbestellungen wirken zum Monatsende
- Tageslauf (C3): fällige Abbestellungen, offene Antworten, Tageslieferung; im neuen Monat
  wird das Universum mit der ersten Lieferung erneut abgerechnet
- Zeitreihen bitemporal mit Stichtagsabfrage, JSON oder CSV (B-06, C1)
- Feldkatalog, Feldlisten, Prüfpfad (D2, D3)
- Anmeldung über Microsoft Entra ID
- Phase 2: Universum folgt den Xentis-Positionen (B1)
- Phase 2: Historie nachladen (C2) als Bestellung vom Typ `BACKFILL` – mit Kostenprognose
  (Kategorie `History`, Annahme: je Instrument und Monat) und Freigabe ab 200 CHF

## Universum folgt den Xentis-Positionen (B1)

Xentis liefert einmal pro Tag eine Positionsdatei. Import über `POST /api/v1/positions/import`
(CSV, Rolle `lizenz.admin`) oder im Frontend; der Abgleich läuft im Tageslauf automatisch
(`BBGPROXY_XENTIS_AUTO_RECONCILE`) oder über `POST /api/v1/universe/reconcile` mit `dryRun`.

- Gehaltene ISINs ausserhalb des Universums werden mit `BBGPROXY_XENTIS_FIELD_SETS`
  (Standard `["PRICING_EOD"]`) bestellt – über den normalen Weg mit Kostenprognose und
  Freigabe ab 200 CHF durch eine Person.
- Vom Abgleich bestellte Instrumente (Quelle `XENTIS`), die nicht mehr gehalten werden, werden
  zum Monatsende abbestellt. Wird eine Position im selben Monat wieder eröffnet, kostet das nichts.
- Manuell bestellte Instrumente fasst der Abgleich nie an.
- Sicherung: Würde mehr als `BBGPROXY_XENTIS_MAX_UNSUBSCRIBE_SHARE` (Standard 20 %) der
  Xentis-Instrumente abbestellt, unterbleiben die Abbestellungen und der Lauf wird als
  fehlgeschlagen gemeldet – meist ist dann die Datei unvollständig.

**Annahme zum Dateiformat** (bitte mit dem tatsächlichen Xentis-Export abgleichen): CSV mit
Semikolon, Kopfzeile `Stichtag;Portfolio;ISIN;Bestand`, Datum `TT.MM.JJJJ` oder ISO, Bestand
in Schweizer Schreibweise (`1'250.50`). Trennzeichen und Spaltennamen sind über
`BBGPROXY_XENTIS_*` einstellbar. Zeilen ohne ISIN (z. B. Konten, OTC) werden übersprungen.

## Bloomberg-Mock

Bis Zugangsdaten und Katalog-ID der Data License vorliegen, spricht der Service mit
`MockBloombergClient` (`src/bloombergproxy/bloomberg.py`). Der Mock ruft Bloomberg nicht auf und

- liefert deterministische Werte für `PX_LAST`, `PX_VOLUME`, `NAME`, `CRNCY`, `ESG_SCORE`,
- meldet ISINs mit falscher Prüfziffer als `INVALID_SECURITY` (sie werden nicht abgerechnet),
- korrigiert bei der Tageslieferung gelegentlich den Vortagespreis, damit Stichtagsabfragen
  etwas zu zeigen haben.

Der Tageslauf wird über `POST /api/v1/deliveries/run` (Rolle `lizenz.admin`) oder im Frontend
ausgelöst; im Betrieb läuft er täglich nach Börsenschluss, z. B. als Databricks-Job.

## Microsoft Entra ID

Entra ID ist der Standard (`BBGPROXY_AUTH_MODE=entra`). Benötigte Einstellungen:

| Variable | Wert |
| --- | --- |
| `BBGPROXY_ENTRA_TENANT_ID` | Tenant-ID |
| `BBGPROXY_ENTRA_API_CLIENT_ID` | Application (client) ID der API-App-Registrierung |
| `BBGPROXY_ENTRA_SPA_CLIENT_ID` | Application (client) ID der Frontend-App-Registrierung |

Einrichtung in Entra ID:

1. **API-App-Registrierung** „BloombergProxy API“: Application ID URI `api://<client-id>`,
   Scope `access_as_user`, App-Rollen `lizenz.read`, `lizenz.order`, `lizenz.approve`,
   `lizenz.admin` (zugelassen für Benutzer/Gruppen und Anwendungen).
2. **Frontend-App-Registrierung** „BloombergProxy“: Plattform *Single-page application* mit
   der Redirect-URI des Frontends; API-Berechtigung auf `access_as_user` der API.
3. **Enterprise App der API:** Benutzern oder Gruppen die Rollen zuweisen. Für die 4-Augen-Freigabe
   brauchen mindestens zwei Personen `lizenz.approve` bzw. `lizenz.order`.
4. **Andere Systeme:** eigene App-Registrierung, Anwendungsberechtigung auf die Rollen,
   Token per Client Credentials mit Scope `api://<api-client-id>/.default`.

Die API prüft Signatur, Aussteller, Audience und Ablauf; Berechtigungen kommen aus dem Claim
`roles`. Das Frontend meldet sich mit MSAL an.

## Offen

- **Bloomberg-Adapter** für die Data License REST API (ersetzt den Mock).
- **Databricks:** Der Service ist bisher nur mit SQLite getestet.
- **Preise:** Die Vertragswerte fehlen noch; Pflege über `PUT /pricing/categories`.
- **Xentis-Dateiformat** bestätigen (siehe oben).
- **Phase 2, weiter:** Datenqualität (D1), Benachrichtigungen (D4), Nutzungsrechte (B4).
- **Abrechnung der Historie** mit dem Vertrag abgleichen (`BBGPROXY_HISTORY_CATEGORY`).
