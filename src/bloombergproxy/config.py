from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BBGPROXY_", env_file=".env")

    # Databricks: databricks://token:<token>@<host>?http_path=<path>&catalog=bbg_license
    database_url: str = "sqlite:///./bloombergproxy.db"

    # Ab diesem Betrag (Mehrkosten pro Bestellung) braucht es eine zweite Freigabe.
    approval_threshold: Decimal = Decimal("200")
    currency: str = "CHF"

    # "entra": Bearer-Token von Microsoft Entra ID (Standard, Betrieb).
    # "header": Benutzer und Scopes aus X-User / X-Scopes (nur Entwicklung, ohne Prüfung).
    auth_mode: str = "entra"
    entra_tenant_id: str = ""
    entra_api_client_id: str = ""  # App-Registrierung der API (Audience, App-Rollen)
    entra_spa_client_id: str = ""  # App-Registrierung des Frontends (SPA)
