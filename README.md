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
- Phase 2: Datenqualität (D1) nach jedem Tageslauf – fehlende Werte, unveränderte Preise
  (`BBGPROXY_DQ_STALE_DAYS`, Standard 3), Ausreisser (`BBGPROXY_DQ_OUTLIER_SHARE`, Standard 20 %);
  Bearbeitung im Frontend (offen, in Bearbeitung, erledigt)
- Phase 2: Benachrichtigungen (D4) – Freigabe ausstehend, Ablehnung, von Bloomberg abgelehnte
  Instrumente, gestoppter Xentis-Abgleich, neue Datenqualitäts-Ausnahmen, fehlgeschlagener
  Tageslauf. Gespeichert und nach dem Commit per Webhook versandt; für Teams einen Workflow
  „Bei Empfang einer Webhookanforderung im Kanal posten“ anlegen und dessen URL als
  `BBGPROXY_NOTIFY_WEBHOOK_URL` setzen (`BBGPROXY_PUBLIC_URL` für den Link in der Karte)
- Phase 2: Nutzungsrechte (B4) je Datenkategorie – Leserolle (Entra-App-Rolle, z. B.
  `daten.esg`) und Weitergabe (sonst kein CSV-Export). Zugriffe auf eingeschränkte Kategorien
  und Verweigerungen stehen im Prüfpfad; `GET /api/v1/entitlements/usage` listet je Monat, wer
  zugegriffen hat (für Lizenzprüfungen)

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

## Stammdaten und mehrere Anbieter (A1, A4)

- Bloomberg-Stammdatenfelder (z. B. Feldliste `REFERENCE`) befüllen Emittent, Instrument und
  Listing (`silver.issuer`, `silver.security`, `silver.listing`) und die Zuordnung aller
  Kennungen (`silver.identifier_xref`). Ein Instrument wird so unter jeder bekannten Kennung
  gefunden – eine per ISIN bestellte Aktie gilt auch bei einer Bestellung per FIGI als Duplikat.
- Daten anderer Anbieter per CSV: `POST /api/v1/vendors/{anbieter}/xref`
  (`Kennungstyp;Kennung;ISIN`, ordnet Anbieter-Kennungen zu) und
  `POST /api/v1/vendors/{anbieter}/values` (`Kennungstyp;Kennung;Datum;Feld;Wert`).
- Golden Copy: Zeitreihen ohne `source` liefern je Tag den Wert der Quelle mit dem höchsten
  Rang (`BBGPROXY_SOURCE_PRECEDENCE`, Standard `["BLOOMBERG"]`, übrige Anbieter alphabetisch).
- Datenqualität: `SOURCE_MISMATCH`, wenn Quellen beim Preis um mehr als 2 % abweichen
  (`BBGPROXY_DQ_SOURCE_TOLERANCE`); `ID_CONFLICT`, wenn eine Kennung zwei Instrumenten
  zugeordnet würde (die Zuordnung bleibt dann unverändert).

## Corporate Actions (A2)

- Bloomberg-Ereignisse kommen im Tageslauf (Ex-Datum 7 Tage zurück bis 90 Tage voraus), andere
  Anbieter per CSV (`POST /api/v1/vendors/{anbieter}/corporate-actions`).
- Jede Meldung wird je Quelle bitemporal gespeichert (`silver.corporate_action`).
- Golden Copy (`GET /api/v1/corporate-actions`): Meldungen zum selben Instrument, derselben Art
  und demselben Ex-Datum bilden ein Ereignis; massgebend ist die ranghöchste Quelle. Abweichende
  Beträge, Verhältnisse, Zahltage oder Status ergeben die Ausnahme `CA_MISMATCH`.
- Ein Split am Ex-Tag erklärt den Preissprung: dann keine `OUTLIER`-Ausnahme.
- Neue oder geänderte Splits und annullierte Ereignisse werden gemeldet.

## ESG mehrerer Anbieter (A3)

- `ESG_SCORE` von Bloomberg (Feldliste `ESG`) und anderen Anbietern (Werte-Import, z. B.
  `ISIN;CH…;21.10.2026;ESG_SCORE;AA`).
- `GET /api/v1/esg` rechnet jeden Score auf 0–100 um (höher = besser) und zeigt Durchschnitt und
  Abweichung; über `BBGPROXY_ESG_DIVERGENCE` (Standard 25 Punkte) gilt ein Instrument als
  abweichend. Skalen je Quelle in `BBGPROXY_ESG_SCALES` – **Annahmen**: Bloomberg 0–10,
  MSCI CCC…AAA, Sustainalytics Risiko 0–50 (niedriger = besser); mit den Verträgen abgleichen.
- Die Nutzungsrechte der Kategorie `ESG` gelten auch hier.

## Betrieb auf Databricks (C4)

- Datenmodell: `db/ddl.sql` im Katalog `bbg_license` ausführen. Der Service spricht Databricks
  über ein SQL Warehouse an: `BBGPROXY_DATABASE_URL=databricks://token:<token>@<host>?http_path=<pfad>&catalog=bbg_license`
  und `pip install -e ".[databricks]"`.
- Zeitgesteuerte Läufe über die Kommandozeile `bloombergproxy-job`:
  - `bloombergproxy-job import-positions <verzeichnis>` importiert neue Xentis-Dateien
    (bereits importierte Dateinamen werden übersprungen), z. B. aus einem Unity-Catalog-Volume;
  - `bloombergproxy-job daily` führt den Tageslauf aus (Xentis-Abgleich, Lieferungen,
    Corporate Actions, Datenqualität, Meldungen).
- `databricks.yml` (Asset Bundle) richtet beides als Job ein: werktags 18:30 Zürcher Zeit,
  Einzelknoten-Cluster, Zugangsdaten aus dem Secret-Scope `bloombergproxy`
  (`database_url`, `notify_webhook_url`). `databricks bundle deploy -t prod`.
- Solange der Mock aktiv ist, teilen Service und Job offene Anfragen über
  `BBGPROXY_BLOOMBERG_MOCK_STATE_DIR` (z. B. ein Volume).

## Bloomberg-Mock

Bis Zugangsdaten und Katalog-ID der Data License vorliegen, spricht der Service mit
`MockBloombergClient` (`src/bloombergproxy/bloomberg.py`). Der Mock ruft Bloomberg nicht auf und

- liefert deterministische Werte für `PX_LAST`, `PX_VOLUME`, `NAME`, `CRNCY`, `ESG_SCORE` und die
  Stammdatenfelder, dazu Quartalsdividenden (rund 70 % der Instrumente) und gelegentliche Splits,
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
- **Databricks:** Service, Job und Bundle sind bisher nur mit SQLite bzw. ohne Workspace geprüft;
  `databricks bundle validate` und ein erster Lauf gegen einen Workspace stehen aus.
- **Preise:** Die Vertragswerte fehlen noch; Pflege über `PUT /pricing/categories`.
- **Xentis-Dateiformat** bestätigen (siehe oben).
- **Teams-Webhook** einrichten (`BBGPROXY_NOTIFY_WEBHOOK_URL`).
- **Nutzungsrechte** gemäss Vertrag erfassen (`PUT /api/v1/entitlements`) und die Rollen
  (z. B. `daten.esg`) in der API-App-Registrierung anlegen.
- **Abrechnung der Historie** mit dem Vertrag abgleichen (`BBGPROXY_HISTORY_CATEGORY`).
