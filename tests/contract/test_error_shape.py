"""One error shape for every non-2xx response. A deliberate 404, 403, 422,
and 500 all come back with the identical top-level field set --
``type``/``title``/``status``/``detail``/``instance``/``request_id`` --
and as ``application/problem+json``, not a mix of shapes depending on
which layer raised.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration

_REQUIRED_FIELDS = {"type", "title", "status", "detail", "instance", "request_id"}


def _assert_problem_shape(response, expected_status: int) -> None:
    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert _REQUIRED_FIELDS.issubset(body.keys())
    assert body["status"] == expected_status
    assert body["request_id"]


async def test_404_not_found_shape(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    r = await client.get("/v1/ingest/jobs/00000000-0000-0000-0000-000000000000", headers=auth_header(token))
    _assert_problem_shape(r, 404)


async def test_403_forbidden_shape(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_b.id}", headers=auth_header(token))
    _assert_problem_shape(r, 403)


async def test_422_validation_error_shape(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_a.id}/documents?cursor=not-a-valid-cursor", headers=auth_header(token))
    _assert_problem_shape(r, 422)


async def test_422_pydantic_validation_error_shape(client):
    r = await client.post("/v1/auth/refresh", json={})
    _assert_problem_shape(r, 422)
    assert "errors" in r.json()


async def test_500_unhandled_error_shape(client, world, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    from app.api.v1 import patients as patients_module

    monkeypatch.setattr(patients_module, "record_access", _boom)
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_a.id}", headers=auth_header(token))
    _assert_problem_shape(r, 500)
    assert "RuntimeError" not in r.text
    assert "simulated failure" not in r.text


async def test_401_unauthenticated_shape(client):
    r = await client.get("/v1/audit")
    _assert_problem_shape(r, 401)
