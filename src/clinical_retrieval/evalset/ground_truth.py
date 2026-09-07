"""Load structured FHIR into per-patient records and answer questions directly
against them. This is the independent ground truth the eval set is built from
— it never reads the clinical notes (see corpus/notes.py), which is the
retrieval corpus these questions get evaluated against (§0 of the build spec).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..corpus.ndjson_loader import iter_resource, strip_reference


@dataclass
class EncounterRecord:
    encounter_id: str
    patient_id: str
    date: str  # ISO datetime, may be None
    type: str | None


@dataclass
class ConditionRecord:
    condition_id: str
    patient_id: str
    encounter_id: str | None
    display: str
    onset: str | None
    abatement: str | None
    recorded_date: str | None


@dataclass
class MedicationRecord:
    medication_id: str
    patient_id: str
    encounter_id: str | None
    display: str
    authored_on: str | None


@dataclass
class ObservationRecord:
    observation_id: str
    patient_id: str
    encounter_id: str | None
    loinc_code: str | None
    display: str
    value: float | None
    unit: str | None
    value_text: str | None
    effective: str | None
    components: dict[str, tuple[float, str]] = field(default_factory=dict)  # display -> (value, unit)


@dataclass
class ImmunizationRecord:
    immunization_id: str
    patient_id: str
    encounter_id: str | None
    display: str
    occurrence: str | None


@dataclass
class AllergyRecord:
    allergy_id: str
    patient_id: str
    substance: str


@dataclass
class PatientData:
    patient_id: str
    name: str
    encounters: list[EncounterRecord] = field(default_factory=list)
    conditions: list[ConditionRecord] = field(default_factory=list)
    medications: list[MedicationRecord] = field(default_factory=list)
    observations: list[ObservationRecord] = field(default_factory=list)
    immunizations: list[ImmunizationRecord] = field(default_factory=list)
    allergies: list[AllergyRecord] = field(default_factory=list)


def _display(codeable_concept: dict | None) -> str:
    if not codeable_concept:
        return "unknown"
    for coding in codeable_concept.get("coding", []):
        if coding.get("display"):
            return coding["display"]
    return codeable_concept.get("text", "unknown")


def load_all_patient_data(raw_dir: str) -> dict[str, PatientData]:
    patients: dict[str, PatientData] = {}

    for p in iter_resource(raw_dir, "Patient"):
        official = next((n for n in p.get("name", []) if n.get("use") == "official"), None)
        official = official or (p.get("name") or [{}])[0]
        given = " ".join(official.get("given", []))
        family = official.get("family", "")
        patients[p["id"]] = PatientData(patient_id=p["id"], name=f"{given} {family}".strip())

    for enc in iter_resource(raw_dir, "Encounter"):
        patient_id = strip_reference(enc.get("subject", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        type_text = (enc.get("type") or [{}])[0].get("text")
        patients[patient_id].encounters.append(
            EncounterRecord(
                encounter_id=enc["id"],
                patient_id=patient_id,
                date=(enc.get("period") or {}).get("start"),
                type=type_text,
            )
        )

    for c in iter_resource(raw_dir, "Condition"):
        patient_id = strip_reference(c.get("subject", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        patients[patient_id].conditions.append(
            ConditionRecord(
                condition_id=c["id"],
                patient_id=patient_id,
                encounter_id=strip_reference(c.get("encounter", {}).get("reference"), "Encounter"),
                display=_display(c.get("code")),
                onset=c.get("onsetDateTime"),
                abatement=c.get("abatementDateTime"),
                recorded_date=c.get("recordedDate"),
            )
        )

    for m in iter_resource(raw_dir, "MedicationRequest"):
        patient_id = strip_reference(m.get("subject", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        patients[patient_id].medications.append(
            MedicationRecord(
                medication_id=m["id"],
                patient_id=patient_id,
                encounter_id=strip_reference(m.get("encounter", {}).get("reference"), "Encounter"),
                display=_display(m.get("medicationCodeableConcept")),
                authored_on=m.get("authoredOn"),
            )
        )

    for o in iter_resource(raw_dir, "Observation"):
        patient_id = strip_reference(o.get("subject", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        loinc = next(
            (c.get("code") for c in o.get("code", {}).get("coding", []) if "loinc" in c.get("system", "")),
            None,
        )
        vq = o.get("valueQuantity")
        components = {}
        for comp in o.get("component", []):
            cvq = comp.get("valueQuantity")
            if cvq is not None:
                components[_display(comp.get("code"))] = (cvq.get("value"), cvq.get("unit"))
        patients[patient_id].observations.append(
            ObservationRecord(
                observation_id=o["id"],
                patient_id=patient_id,
                encounter_id=strip_reference(o.get("encounter", {}).get("reference"), "Encounter"),
                loinc_code=loinc,
                display=_display(o.get("code")),
                value=vq.get("value") if vq else None,
                unit=vq.get("unit") if vq else None,
                value_text=o.get("valueCodeableConcept", {}).get("text"),
                effective=o.get("effectiveDateTime"),
                components=components,
            )
        )

    for i in iter_resource(raw_dir, "Immunization"):
        patient_id = strip_reference(i.get("patient", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        patients[patient_id].immunizations.append(
            ImmunizationRecord(
                immunization_id=i["id"],
                patient_id=patient_id,
                encounter_id=strip_reference(i.get("encounter", {}).get("reference"), "Encounter"),
                display=_display(i.get("vaccineCode")),
                occurrence=i.get("occurrenceDateTime"),
            )
        )

    for a in iter_resource(raw_dir, "AllergyIntolerance"):
        patient_id = strip_reference(a.get("patient", {}).get("reference"), "Patient")
        if patient_id not in patients:
            continue
        patients[patient_id].allergies.append(
            AllergyRecord(allergy_id=a["id"], patient_id=patient_id, substance=_display(a.get("code")))
        )

    for pd in patients.values():
        pd.encounters.sort(key=lambda e: e.date or "")
        pd.conditions.sort(key=lambda c: c.onset or c.recorded_date or "")
        pd.medications.sort(key=lambda m: m.authored_on or "")
        pd.observations.sort(key=lambda o: o.effective or "")

    return patients


def encounters_by_id(patients: dict[str, PatientData]) -> dict[str, EncounterRecord]:
    return {e.encounter_id: e for pd in patients.values() for e in pd.encounters}


def medications_by_encounter(pd: PatientData) -> dict[str, list[MedicationRecord]]:
    out: dict[str, list[MedicationRecord]] = defaultdict(list)
    for m in pd.medications:
        if m.encounter_id:
            out[m.encounter_id].append(m)
    return dict(out)


def conditions_by_encounter(pd: PatientData) -> dict[str, list[ConditionRecord]]:
    out: dict[str, list[ConditionRecord]] = defaultdict(list)
    for c in pd.conditions:
        if c.encounter_id:
            out[c.encounter_id].append(c)
    return dict(out)


def observations_by_encounter(pd: PatientData) -> dict[str, list[ObservationRecord]]:
    out: dict[str, list[ObservationRecord]] = defaultdict(list)
    for o in pd.observations:
        if o.encounter_id:
            out[o.encounter_id].append(o)
    return dict(out)


def immunizations_by_encounter(pd: PatientData) -> dict[str, list[ImmunizationRecord]]:
    out: dict[str, list[ImmunizationRecord]] = defaultdict(list)
    for i in pd.immunizations:
        if i.encounter_id:
            out[i.encounter_id].append(i)
    return dict(out)
