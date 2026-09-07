"""Streaming NDJSON reader for FHIR bulk-export files.

Observation alone is ~70K records across two ~50MB/20MB files. Each NDJSON line
is already a complete, independent JSON object, so a line-by-line generator
gives O(1) memory regardless of file size — never call json.load() on these
files, which would materialize the whole array at once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


def iter_ndjson(path: str | Path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def iter_resource(raw_dir: str | Path, resource_type: str) -> Iterator[dict]:
    """Iterate every record for a resource type, transparently spanning the
    numbered shard files FHIR bulk export uses when a resource exceeds 50MB
    (e.g. Observation.000.ndjson, Observation.001.ndjson)."""
    raw_dir = Path(raw_dir)
    shards = sorted(raw_dir.glob(f"{resource_type}.*.ndjson"))
    if not shards:
        raise FileNotFoundError(f"No {resource_type}.*.ndjson under {raw_dir}")
    for shard in shards:
        yield from iter_ndjson(shard)


def load_resource_index(raw_dir: str | Path, resource_type: str) -> dict[str, dict]:
    """Load an entire resource type into memory keyed by id. Fine for small/medium
    resources (Patient: 120, Encounter/DocumentReference: 7,761); do not use this
    for Observation — stream it with iter_resource instead."""
    return {r["id"]: r for r in iter_resource(raw_dir, resource_type)}


def strip_reference(reference: str | None, prefix: str) -> str | None:
    """'Patient/abc-123' -> 'abc-123'. Returns None for references FHIR can also
    encode as search queries (e.g. 'Practitioner?identifier=...'), which carry no
    resolvable id in this export."""
    if not reference:
        return None
    if reference.startswith(f"{prefix}/"):
        return reference[len(prefix) + 1 :]
    return None


def build_encounter_resource_index(
    raw_dir: str | Path,
    resource_types: tuple[str, ...] = (
        "Condition",
        "MedicationRequest",
        "Procedure",
        "Observation",
        "DiagnosticReport",
        "Immunization",
    ),
) -> dict[str, dict[str, list[str]]]:
    """Build {encounter_id: {resource_type: [resource_id, ...]}} once, by a single
    streaming pass per resource type. Cache the result (see corpus.index) rather
    than rebuilding it per query — Observation alone is 70K records."""
    index: dict[str, dict[str, list[str]]] = {}
    for rtype in resource_types:
        for record in iter_resource(raw_dir, rtype):
            enc_id = strip_reference(record.get("encounter", {}).get("reference"), "Encounter")
            if enc_id is None:
                continue
            index.setdefault(enc_id, {}).setdefault(rtype, []).append(record["id"])
    return index
