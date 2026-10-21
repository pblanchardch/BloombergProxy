from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BBGPROXY_", env_file=".env")

    # Databricks: databricks://token:<token>@<host>?http_path=<path>&catalog=bbg_license
    database_url: str = "sqlite:///./bloombergproxy.db"

    # Ab diesem Betrag (Mehrkosten pro Bestellung) braucht es eine zweite Freigabe.
    approval_threshold: Decimal = Decimal("200")
    currency: str = "CHF"

    # Backfill (C2): Abrechnungskategorie für Historien-Anfragen und maximaler Zeitraum.
    # Annahme: Bloomberg rechnet Historie je Instrument und Monat ab; mit dem Vertrag abgleichen.
    history_category: str = "History"
    backfill_max_days: int = 3650

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

    # "entra": Bearer-Token von Microsoft Entra ID (Standard, Betrieb).
    # "header": Benutzer und Scopes aus X-User / X-Scopes (nur Entwicklung, ohne Prüfung).
    auth_mode: str = "entra"
    entra_tenant_id: str = ""
    entra_api_client_id: str = ""  # App-Registrierung der API (Audience, App-Rollen)
    entra_spa_client_id: str = ""  # App-Registrierung des Frontends (SPA)
