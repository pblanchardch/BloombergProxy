import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from bloombergproxy.auth import EntraVerifier
from bloombergproxy.config import Settings
from bloombergproxy.main import create_app

TENANT = "11111111-1111-1111-1111-111111111111"
API = "22222222-2222-2222-2222-222222222222"
PRIVATE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StaticKeys:
    def get_signing_key_from_jwt(self, token):
        return jwt.PyJWK.from_dict(jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE.public_key(), as_dict=True))


def token(key=PRIVATE, **claims) -> str:
    payload = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": API, "sub": "s1",
               "exp": int(time.time()) + 600, "preferred_username": "alice@firma.ch",
               "roles": ["lizenz.read", "lizenz.order"]} | claims
    return jwt.encode(payload, key, algorithm="RS256")


@pytest.fixture
def entra_client(service):
    settings = Settings(auth_mode="entra", entra_tenant_id=TENANT, entra_api_client_id=API,
                        entra_spa_client_id="33333333-3333-3333-3333-333333333333")
    service.settings = settings
    return TestClient(create_app(settings, service=service,
                                 verifier=EntraVerifier(TENANT, API, keys=StaticKeys())))


def get(client, tok):
    return client.get("/api/v1/instruments", headers={"Authorization": f"Bearer {tok}"})


def test_valid_token_is_accepted(entra_client):
    assert get(entra_client, token()).status_code == 200


def test_api_uri_audience_is_accepted(entra_client):
    assert get(entra_client, token(aud=f"api://{API}")).status_code == 200


@pytest.mark.parametrize("bad", [
    {"aud": "fremde-api"},
    {"iss": "https://login.microsoftonline.com/anderer-tenant/v2.0"},
    {"exp": int(time.time()) - 10},
])
def test_invalid_claims_are_rejected(entra_client, bad):
    assert get(entra_client, token(**bad)).status_code == 401


def test_wrong_signature_is_rejected(entra_client):
    assert get(entra_client, token(key=OTHER)).status_code == 401


def test_missing_token_and_dev_headers_are_rejected(entra_client):
    assert entra_client.get("/api/v1/instruments").status_code == 401
    r = entra_client.get("/api/v1/instruments", headers={"X-User": "alice", "X-Scopes": "lizenz.read"})
    assert r.status_code == 401


def test_roles_decide_permissions_and_name_drives_four_eyes(entra_client):
    reader = token(roles=["lizenz.read"])
    order = {"type": "SUBSCRIBE", "instruments": [{"type": "ISIN", "value": "CH0012032048"}],
             "fieldSets": ["ESG"]}  # ohne Preis -> Freigabe nötig
    assert entra_client.post("/api/v1/orders", json=order,
                             headers={"Authorization": f"Bearer {reader}"}).status_code == 403

    created = entra_client.post("/api/v1/orders", json=order,
                                headers={"Authorization": f"Bearer {token()}"}).json()
    assert created["requestedBy"] == "alice@firma.ch"

    self_approver = token(roles=["lizenz.read", "lizenz.approve"])
    r = entra_client.post(f"/api/v1/orders/{created['id']}/approve",
                          headers={"Authorization": f"Bearer {self_approver}"})
    assert r.status_code == 403

    bob = token(preferred_username="bob@firma.ch", roles=["lizenz.read", "lizenz.approve"])
    r = entra_client.post(f"/api/v1/orders/{created['id']}/approve", headers={"Authorization": f"Bearer {bob}"})
    assert r.json()["approvedBy"] == "bob@firma.ch"


def test_app_token_uses_client_id_as_name(entra_client):
    app_token = token(preferred_username=None, azp="44444444-4444-4444-4444-444444444444")
    order = {"type": "SUBSCRIBE", "instruments": [{"type": "ISIN", "value": "CH0012032048"}],
             "fieldSets": ["PRICING_EOD"]}
    r = entra_client.post("/api/v1/orders", json=order, headers={"Authorization": f"Bearer {app_token}"})
    assert r.json()["requestedBy"] == "44444444-4444-4444-4444-444444444444"


def test_auth_config(entra_client, client):
    cfg = entra_client.get("/api/v1/auth/config").json()
    assert cfg == {"mode": "entra", "tenantId": TENANT, "clientId": "33333333-3333-3333-3333-333333333333",
                   "scopes": [f"api://{API}/access_as_user"]}
    assert client.get("/api/v1/auth/config").json() == {"mode": "header"}


def test_entra_mode_requires_ids():
    with pytest.raises(ValueError):
        create_app(Settings(auth_mode="entra", database_url="sqlite://"))
