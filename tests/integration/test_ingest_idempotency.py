from __future__ import annotations

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration

_BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "test-p-1", "name": [{"given": ["Test"], "family": "One"}]}},
        {"resource": {"resourceType": "Condition", "id": "c1", "code": {"text": "Hypertension"}}},
    ],
}


async def test_missing_idempotency_key_is_rejected(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    r = await client.post("/v1/ingest/bundles", json=_BUNDLE, headers=auth_header(token))
    assert r.status_code == 400


async def test_replay_same_key_same_body_returns_stored_response(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    headers = {**auth_header(token), "Idempotency-Key": "test-key-abc"}

    r1 = await client.post("/v1/ingest/bundles", json=_BUNDLE, headers=headers)
    assert r1.status_code == 202
    job_id = r1.json()["job_id"]

    r2 = await client.post("/v1/ingest/bundles", json=_BUNDLE, headers=headers)
    assert r2.status_code == 202
    assert r2.json()["job_id"] == job_id


async def test_replay_same_key_different_body_is_422(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    headers = {**auth_header(token), "Idempotency-Key": "test-key-xyz"}

    r1 = await client.post("/v1/ingest/bundles", json=_BUNDLE, headers=headers)
    assert r1.status_code == 202

    different_bundle = {**_BUNDLE, "type": "searchset"}
    r2 = await client.post("/v1/ingest/bundles", json=different_bundle, headers=headers)
    assert r2.status_code == 422


async def test_unknown_job_id_is_404(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    r = await client.get("/v1/ingest/jobs/00000000-0000-0000-0000-000000000000", headers=auth_header(token))
    assert r.status_code == 404


async def test_clinician_cannot_ingest(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    headers = {**auth_header(token), "Idempotency-Key": "test-key-clinician-blocked"}
    r = await client.post("/v1/ingest/bundles", json=_BUNDLE, headers=headers)
    assert r.status_code == 403
