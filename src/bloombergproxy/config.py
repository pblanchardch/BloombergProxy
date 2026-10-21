from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BBGPROXY_", env_file=".env")

    # Databricks: databricks://token:<token>@<host>?http_path=<path>&catalog=bbg_license
    database_url: str = "sqlite:///./bloombergproxy.db"

    # Ab diesem Betrag (Mehrkosten pro Bestellung) braucht es eine zweite Freigabe.
    approval_threshold: Decimal = Decimal("200")
    currency: str = "CHF"

    # "header": Benutzer und Scopes kommen aus X-User / X-Scopes (nur Entwicklung).
    # Für den Betrieb durch OAuth2 (z. B. Entra ID) ersetzen.
    auth_mode: str = "header"
