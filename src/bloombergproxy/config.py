from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BBGPROXY_", env_file=".env")

    # Databricks: databricks://token:<token>@<host>?http_path=<path>&catalog=bbg_license
    database_url: str = "sqlite:///./bloombergproxy.db"

    # Ab diesem Betrag (Mehrkosten pro Bestellung) braucht es eine zweite Freigabe.
    approval_threshold: Decimal = Decimal("200")
    currency: str = "CHF"

    # Golden Copy (A4): Rangfolge der Quellen; nicht genannte Anbieter folgen alphabetisch.
    source_precedence: list[str] = ["BLOOMBERG"]

    # ESG (A3): Skala je Quelle für die Umrechnung auf 0–100 (höher = besser). Annahmen – mit den
    # Anbieterverträgen abgleichen. Quellen ohne Eintrag werden angezeigt, aber nicht umgerechnet.
    esg_scales: dict[str, dict] = {
        "BLOOMBERG": {"min": 0, "max": 10, "higher_is_better": True},
        "MSCI": {"letters": ["CCC", "B", "BB", "BBB", "A", "AA", "AAA"]},
        "SUSTAINALYTICS": {"min": 0, "max": 50, "higher_is_better": False},
    }
    esg_divergence: float = 25.0  # Punkte auf der 0–100-Skala

    # Backfill (C2): Abrechnungskategorie für Historien-Anfragen und maximaler Zeitraum.
    # Annahme: Bloomberg rechnet Historie je Instrument und Monat ab; mit dem Vertrag abgleichen.
    history_category: str = "History"
    backfill_max_days: int = 3650

    # Datenqualität (D1)
    dq_stale_days: int = 3          # PX_LAST so viele Liefertage unverändert -> STALE_PRICE
    dq_outlier_share: float = 0.2   # Veränderung zum Vortag über 20 % -> OUTLIER
    dq_source_tolerance: float = 0.02  # Quellen weichen beim Preis um über 2 % ab -> SOURCE_MISMATCH

    # Xentis-Positionsdatei (CSV). Spaltennamen an den tatsächlichen Export anpassen.
    xentis_delimiter: str = ";"
    xentis_col_date: str = "Stichtag"
    xentis_col_portfolio: str = "Portfolio"
    xentis_col_isin: str = "ISIN"
    xentis_col_quantity: str = "Bestand"
    # Feldlisten, mit denen der Abgleich neue Positionen bestellt (B1).
    xentis_field_sets: list[str] = ["PRICING_EOD"]
    # Abgleich im Tageslauf ausführen; sonst nur über POST /universe/reconcile.
    xentis_auto_reconcile: bool = True
    # Sicherung gegen unvollständige Dateien: Würde der Abgleich mehr als diesen Anteil der
    # Xentis-Instrumente abbestellen, bestellt er nichts ab und meldet das.
    xentis_max_unsubscribe_share: float = 0.2

    # Benachrichtigungen (D4): Webhook, z. B. Teams-Workflow; leer = nur im Service sichtbar.
    notify_webhook_url: str = ""
    notify_webhook_format: str = "teams"  # "teams" (Adaptive Card) oder "plain" (JSON)
    public_url: str = ""  # Link in den Meldungen, z. B. https://lizenz.firma.ch

    # "entra": Bearer-Token von Microsoft Entra ID (Standard, Betrieb).
    # "header": Benutzer und Scopes aus X-User / X-Scopes (nur Entwicklung, ohne Prüfung).
    auth_mode: str = "entra"
    entra_tenant_id: str = ""
    entra_api_client_id: str = ""  # App-Registrierung der API (Audience, App-Rollen)
    entra_spa_client_id: str = ""  # App-Registrierung des Frontends (SPA)
