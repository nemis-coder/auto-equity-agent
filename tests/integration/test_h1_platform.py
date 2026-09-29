from __future__ import annotations

import hashlib
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.main import create_app
from app.persistence.models import Actor, ActorRole
from app.security.auth import hash_token
from app.storage.interfaces import StorageError
from app.storage.s3 import S3DocumentStore


@pytest.fixture(scope="module")
def client(settings, demo_credentials):
    with TestClient(create_app(settings)) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_checks_real_database_and_bucket(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {
        "database": "ok",
        "document_store": "ok",
        "telemetry": "local_only",
    }


@pytest.mark.parametrize(
    ("alias", "role"),
    [("cliente-ana", "CUSTOMER"), ("asesor-1", "ADVISOR"), ("agente-servicio", "SERVICE")],
)
def test_me_returns_identity_from_token(client, demo_credentials, alias, role):
    r = client.get("/me", headers=_auth(demo_credentials[alias]))
    assert r.status_code == 200
    body = r.json()
    assert body["alias"] == alias and body["role"] == role
    assert demo_credentials[alias] not in r.text


@pytest.mark.parametrize("headers", [{}, _auth("token-inexistente"), {"Authorization": "Basic x"}])
def test_me_rejects_missing_or_invalid_token_with_uniform_error(client, headers):
    r = client.get("/me", headers=headers)
    assert r.status_code == 401
    body = r.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert body["retryable"] is False
    assert body["correlation_id"] == r.headers["X-Correlation-Id"]
    uuid.UUID(body["correlation_id"])


def test_role_header_cannot_escalate(client, demo_credentials):
    r = client.get("/me", headers={**_auth(demo_credentials["cliente-ana"]), "X-Role": "ADVISOR"})
    assert r.json()["role"] == "CUSTOMER"


def test_inactive_actor_is_rejected(client, test_database_url, demo_credentials):
    token = demo_credentials["cliente-beto"]
    engine = create_engine(test_database_url)
    with Session(engine) as s, s.begin():
        s.execute(update(Actor).where(Actor.alias == "cliente-beto").values(active=False))
    try:
        assert client.get("/me", headers=_auth(token)).status_code == 401
    finally:
        with Session(engine) as s, s.begin():
            s.execute(update(Actor).where(Actor.alias == "cliente-beto").values(active=True))
        engine.dispose()


def test_tokens_are_stored_only_as_hashes(test_database_url, demo_credentials):
    engine = create_engine(test_database_url)
    with Session(engine) as s:
        hashes = set(s.scalars(select(Actor.token_hash)))
    engine.dispose()
    for token in demo_credentials.values():
        assert token not in hashes
        assert hash_token(token) in hashes


def test_customer_role_requires_customer_id(test_database_url):
    engine = create_engine(test_database_url)
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        s.add(Actor(alias="sin-cliente", role=ActorRole.CUSTOMER, token_hash="0" * 64))
    engine.dispose()


def test_init_demo_is_idempotent(test_database_url, demo_credentials, tmp_path):
    from scripts.init_demo import init_demo

    path = tmp_path / "creds.json"
    path.write_text("{}")
    assert init_demo(test_database_url, path, rotate=set()) == {}


@pytest.fixture
def store(settings):
    return S3DocumentStore(
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
        bucket=settings.s3_bucket,
        access_key_id=settings.s3_access_key_id,
        secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        namespace=settings.s3_namespace,
    )


async def test_store_roundtrip_verifies_sha256(store):
    key = f"it-tests/{uuid.uuid4()}"
    content = b"%PDF-1.4 sintetico"
    digest = hashlib.sha256(content).hexdigest()
    await store.put(key, content, digest)
    try:
        assert await store.get(key, digest) == content
        with pytest.raises(StorageError):
            await store.get(key, "0" * 64)
    finally:
        await store.delete(key)


async def test_store_rejects_put_with_wrong_hash(store):
    with pytest.raises(StorageError):
        await store.put(f"it-tests/{uuid.uuid4()}", b"abc", "0" * 64)


def test_app_user_cannot_manage_other_buckets(settings):
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
    )
    with pytest.raises(ClientError) as exc:
        s3.create_bucket(Bucket=f"otro-{uuid.uuid4().hex[:8]}")
    assert exc.value.response["Error"]["Code"] == "AccessDenied"


def test_readyz_reports_unavailable_storage(settings, demo_credentials):
    broken = S3DocumentStore(
        endpoint_url="http://127.0.0.1:1",
        region="us-east-1",
        bucket="x",
        access_key_id="x",
        secret_access_key="x",
    )
    with TestClient(create_app(settings, store=broken)) as c:
        r = c.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["document_store"] == "unavailable"
