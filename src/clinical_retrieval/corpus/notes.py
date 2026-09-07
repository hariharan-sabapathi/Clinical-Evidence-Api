"""Extract clinical notes from DocumentReference resources.

Every note in this export carries the same six markdown-style section headers
(Chief Complaint, History of Present Illness, Social History, Allergies,
Medications, Assessment and Plan). Base64-decode the attachment, split on those
headers, and keep the FHIR linkage (patient, encounter, date, note type) that
makes structured ground truth possible.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from .ndjson_loader import iter_resource, strip_reference

SECTION_ORDER = (
    "Chief Complaint",
    "History of Present Illness",
    "Social History",
    "Allergies",
    "Medications",
    "Assessment and Plan",
)

# Top-level sections use a single '# Header'; the Assessment and Plan section
# also contains a nested '## Plan' subheading that must stay inside it, not
# split out as its own section — the negative lookahead excludes '##'.
_HEADER_RE = re.compile(r"^#(?!#)\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class Note:
    note_id: str
    patient_id: str
    encounter_id: str
    date: str | None
    note_types: tuple[str, ...]
    text: str
    sections: dict[str, str] = field(default_factory=dict)


def _decode_attachment(doc_ref: dict) -> str:
    parts = []
    for content in doc_ref.get("content", []):
        data = content.get("attachment", {}).get("data")
        if data:
            parts.append(base64.b64decode(data).decode("utf-8"))
    return "\n\n".join(parts)


def _split_sections(text: str) -> dict[str, str]:
    """Split a note on '# Header' lines into {header: body}. Headers not in
    SECTION_ORDER are kept too (verbatim), so a corpus drift doesn't silently
    drop content — callers that need exactly the six known sections should
    filter with `sections.get(name, "")`."""
    matches = list(_HEADER_RE.finditer(text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def extract_notes(raw_dir: str) -> list[Note]:
    """One Note per DocumentReference. Every DocumentReference in this export
    links to exactly one encounter (verified: 7,761 records, 7,761 unique
    encounter references, including resources marked status='superseded' —
    Synthea marks all but a patient's most recent note superseded even though
    each is still the sole note for its own encounter)."""
    notes: list[Note] = []
    for doc in iter_resource(raw_dir, "DocumentReference"):
        patient_id = strip_reference(doc.get("subject", {}).get("reference"), "Patient")
        enc_refs = doc.get("context", {}).get("encounter", [])
        encounter_id = strip_reference(enc_refs[0]["reference"], "Encounter") if enc_refs else None
        if patient_id is None or encounter_id is None:
            continue
        note_types = tuple(
            c.get("display", "")
            for c in doc.get("type", {}).get("coding", [])
            if c.get("display")
        )
        text = _decode_attachment(doc)
        notes.append(
            Note(
                note_id=doc["id"],
                patient_id=patient_id,
                encounter_id=encounter_id,
                date=doc.get("date"),
                note_types=note_types,
                text=text,
                sections=_split_sections(text),
            )
        )
    return notes
