"""The test that matters more than any other test in this repo (see
README's "Security tests" section). If exactly one test in this whole
suite gets read in a code review, it should be this file.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_clinician_cannot_read_unassigned_patient_documents(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_b.id}/documents", headers=auth_header(token))
    assert r.status_code == 403
    assert str(world.patient_b.id) not in r.text
    assert "fracture" not in r.text.lower()
    assert "wrist" not in r.text.lower()


async def test_clinician_cannot_read_unassigned_patient_record(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.get(f"/v1/patients/{world.patient_b.id}", headers=auth_header(token))
    assert r.status_code == 403
    assert str(world.patient_b.id) not in r.text


async def test_retrieval_never_returns_unassigned_evidence(client, world):
    """Even when clinician_a asks a question whose keywords match patient
    B's chart (not patient A's), the query is scoped to patient A's own
    documents inside the endpoint's authorization check and inside the SQL
    query itself -- so it can never surface patient B's evidence, and
    the request is rejected before retrieval runs at all."""
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.post(
        f"/v1/patients/{world.patient_b.id}/query", json={"q": "fractured wrist"}, headers=auth_header(token)
    )
    assert r.status_code == 403
    assert "wrist" not in r.text.lower()


async def test_query_citations_are_scoped_to_the_requested_patient(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.post(
        f"/v1/patients/{world.patient_a.id}/query", json={"q": "diabetes"}, headers=auth_header(token)
    )
    assert r.status_code == 200
    citations = r.json()["citations"]
    assert len(citations) > 0
    assert all(c["patient_id"] == str(world.patient_a.id) for c in citations)


async def test_optimistic_lock_update_on_unassigned_patient_is_forbidden(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    r = await client.patch(
        f"/v1/patients/{world.patient_b.id}/documents/{world.doc_b.id}",
        json={"reviewed": True},
        headers={**auth_header(token), "If-Match": '"1"'},
    )
    assert r.status_code == 403


async def test_auditor_cannot_read_clinical_documents(client, world):
    """Auditors see *that* access happened, never the clinical content."""
    token = await login(client, world.auditor.email, world.auditor_password)
    r = await client.get(f"/v1/patients/{world.patient_a.id}/documents", headers=auth_header(token))
    assert r.status_code == 403


async def test_admin_can_read_across_patients_for_assignment_management(client, world):
    token = await login(client, world.admin.email, world.admin_password)
    r = await client.get("/v1/patients", headers=auth_header(token))
    assert r.status_code == 200
    ids = {p["id"] for p in r.json()["items"]}
    assert str(world.patient_a.id) in ids
    assert str(world.patient_b.id) in ids


async def test_unauthenticated_request_is_rejected(client, world):
    r = await client.get(f"/v1/patients/{world.patient_a.id}/documents")
    assert r.status_code == 401
