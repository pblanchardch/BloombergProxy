"""Anmeldung: Microsoft Entra ID im Betrieb, Header im Entwicklungsmodus.

Berechtigungen sind App-Rollen der API-App-Registrierung in Entra ID, gleich benannt wie die
Scopes der OpenAPI-Spezifikation: lizenz.read, lizenz.order, lizenz.approve, lizenz.admin.
Sie kommen im Claim ``roles`` – für Benutzer (über die Zuweisung in der Enterprise App) wie
für andere Systeme (Client Credentials).
"""

from dataclasses import dataclass
from typing import Protocol

import jwt


@dataclass(frozen=True)
class User:
    name: str
    scopes: frozenset[str]


class AuthError(Exception):
    pass


class SigningKeys(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


class EntraVerifier:
    def __init__(self, tenant_id: str, api_client_id: str, keys: SigningKeys | None = None):
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.audiences = [api_client_id, f"api://{api_client_id}"]
        self.keys = keys or jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True)

    def verify(self, token: str) -> User:
        try:
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audiences, issuer=self.issuer,
                                options={"require": ["exp", "iss", "aud"]})
        except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
            raise AuthError(f"Token ungültig: {e}") from e
        # Benutzer: Anmeldename; andere Systeme (Client Credentials): ID der aufrufenden App.
        name = claims.get("preferred_username") or claims.get("upn") or claims.get("azp") or claims["sub"]
        return User(name, frozenset(claims.get("roles", [])))
