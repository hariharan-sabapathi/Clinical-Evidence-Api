"""Serialize each encounter's linked structured FHIR resources into readable
text — the `structured` chunk variant, built to be directly comparable to the
narrative notes in `notes.py` over the same encounters and questions.

Code display strings (SNOMED/LOINC/RxNorm) are already present inline on every
coding in this Synthea export, so no external terminology service is needed —
this module resolves them by reading `coding[].display` / `code.text`, and
normalizes dates to `YYYY-MM-DD` and quantities to `value unit`.

One streaming pass per resource type accumulates formatted lines directly into
a per-encounter dict, rather than building a second full in-memory index of
Observation (70K records) — see the engineering note in ndjson_loader.py.
"""

from __future__ import annotations

from collections import defaultdict

from .ndjson_loader import iter_resource, load_resource_index, strip_reference


def _date(dt: str | None) -> str:
    if not dt:
        return "unknown date"
    return dt[:10]


def _display(codeable_concept: dict | None) -> str:
    if not codeable_concept:
        return "unknown"
    for coding in codeable_concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return codeable_concept.get("text", "unknown")


def load_patient_names(raw_dir: str) -> dict[str, str]:
    names = {}
    for p in iter_resource(raw_dir, "Patient"):
        official = next((n for n in p.get("name", []) if n.get("use") == "official"), None)
        official = official or (p.get("name") or [{}])[0]
        given = " ".join(official.get("given", []))
        family = official.get("family", "")
        names[p["id"]] = f"{given} {family}".strip()
    return names


def load_patient_allergies(raw_dir: str) -> dict[str, list[str]]:
    """AllergyIntolerance has no encounter reference — it links to patient only
    (§1, spec). Grouped here so every encounter's structured text can carry the
    same patient-level allergy line the narrative notes repeat on every visit."""
    allergies: dict[str, list[str]] = defaultdict(list)
    for a in iter_resource(raw_dir, "AllergyIntolerance"):
        patient_id = strip_reference(a.get("patient", {}).get("reference"), "Patient")
        if patient_id:
            allergies[patient_id].append(_display(a.get("code")))
    return dict(allergies)


def build_encounter_structured_text(raw_dir: str) -> dict[str, str]:
    encounters = load_resource_index(raw_dir, "Encounter")
    patient_names = load_patient_names(raw_dir)
    patient_allergies = load_patient_allergies(raw_dir)

    sections: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for c in iter_resource(raw_dir, "Condition"):
        enc_id = strip_reference(c.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        status = (c.get("clinicalStatus", {}).get("coding") or [{}])[0].get("code", "unknown")
        line = f"{_display(c.get('code'))} — status {status}, onset {_date(c.get('onsetDateTime'))}"
        if c.get("abatementDateTime"):
            line += f", resolved {_date(c['abatementDateTime'])}"
        sections[enc_id]["Diagnoses"].append(line)

    for m in iter_resource(raw_dir, "MedicationRequest"):
        enc_id = strip_reference(m.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        med = _display(m.get("medicationCodeableConcept"))
        line = f"{med} — {m.get('status', 'unknown')}, prescribed {_date(m.get('authoredOn'))}"
        sections[enc_id]["Medications"].append(line)

    for p in iter_resource(raw_dir, "Procedure"):
        enc_id = strip_reference(p.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        when = p.get("performedPeriod", {}).get("start") or p.get("performedDateTime")
        line = f"{_display(p.get('code'))} — {p.get('status', 'unknown')}, performed {_date(when)}"
        sections[enc_id]["Procedures"].append(line)

    for o in iter_resource(raw_dir, "Observation"):
        enc_id = strip_reference(o.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        name = _display(o.get("code"))
        vq = o.get("valueQuantity")
        if vq is not None:
            value = f"{vq.get('value')} {vq.get('unit', '')}".strip()
        else:
            value = o.get("valueCodeableConcept", {}).get("text") or o.get("valueString") or "no value recorded"
        line = f"{name}: {value} ({_date(o.get('effectiveDateTime'))})"
        sections[enc_id]["Observations"].append(line)

    for d in iter_resource(raw_dir, "DiagnosticReport"):
        if d.get("presentedForm"):
            continue  # this is a duplicate of the narrative note attachment, not a structured panel
        enc_id = strip_reference(d.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        line = f"{_display(d.get('code'))} ordered {_date(d.get('effectiveDateTime'))}"
        sections[enc_id]["Diagnostic Reports"].append(line)

    for i in iter_resource(raw_dir, "Immunization"):
        enc_id = strip_reference(i.get("encounter", {}).get("reference"), "Encounter")
        if not enc_id:
            continue
        line = f"{_display(i.get('vaccineCode'))} — {i.get('status', 'unknown')}, given {_date(i.get('occurrenceDateTime'))}"
        sections[enc_id]["Immunizations"].append(line)

    section_order = ("Diagnoses", "Medications", "Procedures", "Observations", "Diagnostic Reports", "Immunizations")

    out: dict[str, str] = {}
    for enc_id, enc in encounters.items():
        patient_id = strip_reference(enc.get("subject", {}).get("reference"), "Patient")
        parts = [f"Encounter {enc_id} — {_date(enc.get('period', {}).get('start'))}"]
        parts.append(f"Patient: {patient_names.get(patient_id, patient_id)}")

        allergies = patient_allergies.get(patient_id)
        parts.append("Allergies: " + (", ".join(allergies) if allergies else "No Known Allergies"))

        enc_sections = sections.get(enc_id, {})
        for name in section_order:
            items = enc_sections.get(name)
            if items:
                parts.append(f"{name}:\n" + "\n".join(f"- {it}" for it in items))
        out[enc_id] = "\n\n".join(parts)

    return out
