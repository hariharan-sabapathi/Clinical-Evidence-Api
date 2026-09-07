"""/docs (OpenAPI) must actually describe the API: every path has at least
one documented response example, and the schema itself is valid enough for
FastAPI to have generated it without error (which it does eagerly the
first time /openapi.json is requested).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_openapi_schema_is_served(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "Clinical Evidence API"
    assert "/v1/auth/token" in schema["paths"]
    assert "/v1/patients/{patient_id}/query" in schema["paths"]


async def test_every_path_has_a_response_example_or_schema(client):
    r = await client.get("/openapi.json")
    schema = r.json()
    components_schemas = schema.get("components", {}).get("schemas", {})

    def _contains_example(node, seen: set) -> bool:
        """Recursively walk a schema fragment (including $ref indirection
        through components.schemas) looking for an example anywhere in the
        tree -- a per-field example (how PatientOut/DocumentOut/etc. declare
        theirs) counts just as much as a top-level one."""
        if isinstance(node, dict):
            if "example" in node or "examples" in node:
                return True
            ref = node.get("$ref")
            if ref:
                model_name = ref.rsplit("/", 1)[-1]
                if model_name in seen:
                    return False
                seen.add(model_name)
                return _contains_example(components_schemas.get(model_name, {}), seen)
            return any(_contains_example(v, seen) for v in node.values())
        if isinstance(node, list):
            return any(_contains_example(v, seen) for v in node)
        return False

    def _schema_has_example(response_schema: dict) -> bool:
        return _contains_example(response_schema, set())

    missing = []
    for path, methods in schema["paths"].items():
        if path in ("/healthz", "/readyz", "/metrics"):
            continue
        for method, operation in methods.items():
            responses = operation.get("responses", {})
            success_responses = [v for k, v in responses.items() if k.startswith("2")]
            if success_responses and not any(_schema_has_example(r) for r in success_responses):
                missing.append(f"{method.upper()} {path}")

    assert not missing, f"endpoints missing a response example: {missing}"


async def test_docs_ui_renders(client):
    r = await client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
