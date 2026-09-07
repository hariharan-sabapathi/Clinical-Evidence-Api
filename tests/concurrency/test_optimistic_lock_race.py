"""Two concurrent PATCHes against the same document, same starting ETag:
exactly one must win (200), the other must see 409. This is the whole
value of ``WHERE id = :id AND version = :version`` -- the UPDATE itself is
atomic, so there is no interleaving under which both succeed or both fail.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import auth_header, login

pytestmark = pytest.mark.integration


async def test_exactly_one_concurrent_patch_succeeds(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    headers = {**auth_header(token), "If-Match": '"1"'}
    url = f"/v1/patients/{world.patient_a.id}/documents/{world.doc_a.id}"

    responses = await asyncio.gather(
        client.patch(url, json={"text": "race-A"}, headers=headers),
        client.patch(url, json={"text": "race-B"}, headers=headers),
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 409]

    winner = next(r for r in responses if r.status_code == 200)
    assert winner.json()["version"] == 2
    assert winner.headers["etag"] == '"2"'


async def test_many_concurrent_patches_from_the_same_starting_version_yield_one_winner(client, world):
    token = await login(client, world.clinician_a.email, world.clinician_a_password)
    headers = {**auth_header(token), "If-Match": '"1"'}
    url = f"/v1/patients/{world.patient_a.id}/documents/{world.doc_a.id}"

    responses = await asyncio.gather(*[client.patch(url, json={"text": f"race-{i}"}, headers=headers) for i in range(10)])
    successes = [r for r in responses if r.status_code == 200]
    conflicts = [r for r in responses if r.status_code == 409]

    assert len(successes) == 1
    assert len(conflicts) == 9
