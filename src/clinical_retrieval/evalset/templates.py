"""Tier 1/2/3 question templates. Each generator queries `PatientData` (built
straight from structured FHIR, independent of the notes corpus) and yields
raw candidates — dicts, not yet EvalQuestion objects, since they still need
the groundedness filter (grounding.py) and permissive-set computation
(permissive.py) before they're eval-ready.

A candidate's `answer_terms` are the literal entity strings (condition/
medication/vaccine display names) the grounding filter checks for in note
text — not the full natural-language `answer` sentence, since notes restate
entity names verbatim but never restate a question's English phrasing.
"""

from __future__ import annotations

import random
from collections import defaultdict

from .ground_truth import (
    PatientData,
    conditions_by_encounter,
    immunizations_by_encounter,
    medications_by_encounter,
    observations_by_encounter,
)


def _d(iso: str | None) -> str:
    return iso[:10] if iso else "an unknown date"


def _year(iso: str | None) -> str | None:
    return iso[:4] if iso else None


def _mk(
    tier: int,
    template_id: str,
    question_type: str,
    question: str,
    answer: str,
    answer_terms: list[str],
    patient_id: str,
    gold_encounter_ids: list[str],
    gold_resource_ids: list[str],
    answerable: bool = True,
) -> dict:
    return {
        "tier": tier,
        "template_id": template_id,
        "question_type": question_type,
        "question": question,
        "answer": answer,
        "answer_terms": answer_terms,
        "patient_id": patient_id,
        "gold_encounter_ids": gold_encounter_ids,
        "gold_resource_ids": gold_resource_ids,
        "answerable": answerable,
        "generation": "programmatic",
    }


# ---------------------------------------------------------------------------
# Tier 1 — single-hop factual
# ---------------------------------------------------------------------------


def gen_medication_lookup(pd: PatientData, rng: random.Random) -> list[dict]:
    by_enc = medications_by_encounter(pd)
    enc_by_id = {e.encounter_id: e for e in pd.encounters}
    out = []
    for enc_id, meds in by_enc.items():
        enc = enc_by_id.get(enc_id)
        if enc is None or not meds:
            continue
        displays = sorted({m.display for m in meds})
        q = f"What medications was {pd.name} prescribed during their {_d(enc.date)} {enc.type or 'visit'}?"
        a = "; ".join(displays)
        out.append(
            _mk(1, "t1_medication_lookup", "medication_lookup", q, a, displays, pd.patient_id,
                [enc_id], [m.medication_id for m in meds])
        )
    return out


def gen_blood_pressure_lookup(pd: PatientData, rng: random.Random) -> list[dict]:
    by_enc = observations_by_encounter(pd)
    enc_by_id = {e.encounter_id: e for e in pd.encounters}
    out = []
    for enc_id, obs_list in by_enc.items():
        enc = enc_by_id.get(enc_id)
        if enc is None:
            continue
        for o in obs_list:
            if o.loinc_code != "85354-9" or not o.components:
                continue
            systolic = next((v for k, v in o.components.items() if "Systolic" in k), None)
            diastolic = next((v for k, v in o.components.items() if "Diastolic" in k), None)
            if systolic is None or diastolic is None:
                continue
            q = f"What was {pd.name}'s blood pressure at their {_d(enc.date)} visit?"
            a = f"{systolic[0]:.0f}/{diastolic[0]:.0f} {systolic[1]}"
            terms = [f"{systolic[0]:.0f}", f"{diastolic[0]:.0f}"]
            out.append(
                _mk(1, "t1_blood_pressure", "vital_lookup", q, a, terms, pd.patient_id,
                    [enc_id], [o.observation_id], answerable=True)
            )
    return out


def gen_diagnosis_lookup(pd: PatientData, rng: random.Random) -> list[dict]:
    by_enc = conditions_by_encounter(pd)
    enc_by_id = {e.encounter_id: e for e in pd.encounters}
    out = []
    for enc_id, conds in by_enc.items():
        enc = enc_by_id.get(enc_id)
        if enc is None or not conds:
            continue
        displays = sorted({c.display for c in conds})
        q = f"What was {pd.name} diagnosed with during their {_d(enc.date)} {enc.type or 'visit'}?"
        a = "; ".join(displays)
        out.append(
            _mk(1, "t1_diagnosis_lookup", "diagnosis_lookup", q, a, displays, pd.patient_id,
                [enc_id], [c.condition_id for c in conds])
        )
    return out


def gen_immunization_lookup(pd: PatientData, rng: random.Random) -> list[dict]:
    by_enc = immunizations_by_encounter(pd)
    enc_by_id = {e.encounter_id: e for e in pd.encounters}
    out = []
    for enc_id, imms in by_enc.items():
        enc = enc_by_id.get(enc_id)
        if enc is None or not imms:
            continue
        displays = sorted({i.display for i in imms})
        q = f"What immunization did {pd.name} receive on {_d(enc.date)}?"
        a = "; ".join(displays)
        out.append(
            _mk(1, "t1_immunization_lookup", "immunization_lookup", q, a, displays, pd.patient_id,
                [enc_id], [i.immunization_id for i in imms])
        )
    return out


# ---------------------------------------------------------------------------
# Tier 2 — multi-hop / temporal
# ---------------------------------------------------------------------------


def gen_conditions_after_medication(pd: PatientData, rng: random.Random) -> list[dict]:
    out = []
    for med in pd.medications:
        if not med.authored_on:
            continue
        later = [c for c in pd.conditions if c.onset and c.onset > med.authored_on]
        if not later:
            continue
        later = later[:5]
        displays = [f"{c.display} ({_d(c.onset)})" for c in later]
        terms = [c.display for c in later]
        q = f"Which conditions was {pd.name} diagnosed with after starting {med.display}?"
        a = "; ".join(displays)
        gold_encs = sorted({c.encounter_id for c in later if c.encounter_id})
        out.append(
            _mk(2, "t2_conditions_after_medication", "temporal_medication_to_condition", q, a,
                terms, pd.patient_id, gold_encs, [c.condition_id for c in later])
        )
    return out


def gen_condition_year_count(pd: PatientData, rng: random.Random) -> list[dict]:
    by_display: dict[str, list] = defaultdict(list)
    for c in pd.conditions:
        by_display[c.display].append(c)
    out = []
    for display, occurrences in by_display.items():
        if len(occurrences) < 2:
            continue
        by_year: dict[str, list] = defaultdict(list)
        for c in occurrences:
            y = _year(c.onset or c.recorded_date)
            if y:
                by_year[y].append(c)
        for year, in_year in by_year.items():
            q = f"How many times was {pd.name} seen for {display} in {year}?"
            a = str(len(in_year))
            gold_encs = sorted({c.encounter_id for c in in_year if c.encounter_id})
            out.append(
                _mk(2, "t2_condition_year_count", "temporal_aggregation", q, a,
                    [display], pd.patient_id, gold_encs, [c.condition_id for c in in_year])
            )
    return out


def gen_medication_before_condition(pd: PatientData, rng: random.Random) -> list[dict]:
    out = []
    for cond in pd.conditions:
        if not cond.onset:
            continue
        earlier = [m for m in pd.medications if m.authored_on and m.authored_on < cond.onset]
        if not earlier:
            continue
        last_med = earlier[-1]
        q = f"What medication was {pd.name} on most recently before being diagnosed with {cond.display}?"
        a = f"{last_med.display} (prescribed {_d(last_med.authored_on)})"
        out.append(
            _mk(2, "t2_medication_before_condition", "temporal_condition_to_medication", q, a,
                [last_med.display], pd.patient_id,
                [last_med.encounter_id] if last_med.encounter_id else [], [last_med.medication_id])
        )
    return out


# ---------------------------------------------------------------------------
# Tier 3 — aggregation / negation
# ---------------------------------------------------------------------------


COMMON_SUBSTANCES = [
    "Shellfish (substance)", "Peanut (substance)", "Penicillin (substance)",
    "Latex (substance)", "Eggs (substance)", "Bee venom (substance)",
    "Tree nut (substance)", "Grass pollen (substance)", "Aspirin (substance)",
    "Mold (organism)",
]


def gen_allergy_negation(pd: PatientData, rng: random.Random) -> list[dict]:
    if pd.allergies or not pd.encounters:
        return []
    substance = rng.choice(COMMON_SUBSTANCES)
    q = f"Has {pd.name} ever had a documented allergy to {substance.split(' (')[0]}?"
    a = f"No — {pd.name} has no documented allergies."
    # AllergyIntolerance has no encounter link (§1) — "gold" is every note,
    # since every one of a patient's notes independently restates their
    # allergy status (usually "No Known Allergies") in its own Allergies
    # section. A single reference encounter would understate groundedness.
    all_encs = [e.encounter_id for e in pd.encounters]
    return [
        _mk(3, "t3_allergy_negation", "allergy_negation", q, a, ["No Known Allergies"],
            pd.patient_id, all_encs, [])
    ]


NON_SUBSTANCE_ALLERGY_CODES = {"Allergic disposition (finding)"}


def gen_allergy_positive(pd: PatientData, rng: random.Random) -> list[dict]:
    out = []
    if not pd.encounters:
        return out
    for allergy in pd.allergies:
        if allergy.substance in NON_SUBSTANCE_ALLERGY_CODES:
            continue  # a generic "tendency to allergies" finding, not an answerable substance
        q = f"Has {pd.name} ever had a documented allergy to {allergy.substance.split(' (')[0]}?"
        a = f"Yes — {pd.name} has a documented allergy to {allergy.substance}."
        # Same reasoning as gen_allergy_negation: no encounter link, so check
        # groundedness against every note rather than an arbitrary one.
        all_encs = [e.encounter_id for e in pd.encounters]
        out.append(
            _mk(3, "t3_allergy_positive", "allergy_positive", q, a, [allergy.substance],
                pd.patient_id, all_encs, [allergy.allergy_id])
        )
    return out


TREND_LABS = {
    "4548-4": "Hemoglobin A1c",
    "2339-0": "Glucose",
    "38483-4": "Creatinine",
    "33914-3": "Glomerular filtration rate (GFR)",
}


def gen_lab_trend(pd: PatientData, rng: random.Random) -> list[dict]:
    out = []
    by_code: dict[str, list] = defaultdict(list)
    for o in pd.observations:
        if o.loinc_code in TREND_LABS and o.value is not None:
            by_code[o.loinc_code].append(o)
    for code, obs_list in by_code.items():
        years = {_year(o.effective) for o in obs_list if o.effective}
        if len(years) < 3 or len(obs_list) < 3:
            continue
        first, last = obs_list[0], obs_list[-1]
        if first.value == 0:
            continue
        pct_change = (last.value - first.value) / abs(first.value) * 100
        direction = "increased" if pct_change > 10 else "decreased" if pct_change < -10 else "stayed roughly stable"
        lab_name = TREND_LABS[code]
        q = f"What is the trend in {pd.name}'s {lab_name} over their documented visits?"
        a = (
            f"{lab_name} {direction} from {first.value:.1f} {first.unit or ''} on {_d(first.effective)} "
            f"to {last.value:.1f} {last.unit or ''} on {_d(last.effective)}."
        ).replace("  ", " ")
        gold_encs = sorted({o.encounter_id for o in (first, last) if o.encounter_id})
        out.append(
            _mk(3, "t3_lab_trend", "lab_trend", q, a, [lab_name], pd.patient_id,
                gold_encs, [first.observation_id, last.observation_id])
        )
    return out


def gen_total_condition_count(pd: PatientData, rng: random.Random) -> list[dict]:
    by_display: dict[str, list] = defaultdict(list)
    for c in pd.conditions:
        by_display[c.display].append(c)
    out = []
    for display, occurrences in by_display.items():
        if len(occurrences) < 2:
            continue
        q = f"In total, how many times has {pd.name} been diagnosed with {display}?"
        a = str(len(occurrences))
        gold_encs = sorted({c.encounter_id for c in occurrences if c.encounter_id})
        out.append(
            _mk(3, "t3_total_condition_count", "aggregation", q, a, [display],
                pd.patient_id, gold_encs, [c.condition_id for c in occurrences])
        )
    return out


TIER1_GENERATORS = [gen_medication_lookup, gen_blood_pressure_lookup, gen_diagnosis_lookup, gen_immunization_lookup]
TIER2_GENERATORS = [gen_conditions_after_medication, gen_condition_year_count, gen_medication_before_condition]
TIER3_GENERATORS = [gen_allergy_negation, gen_allergy_positive, gen_lab_trend, gen_total_condition_count]


def generate_candidates(
    patients: dict[str, PatientData], per_tier_target: int, seed: int = 13
) -> list[dict]:
    """Generate candidate questions across all tiers, then subsample each
    tier's generator pool down to roughly `per_tier_target` so no single
    generator dominates a tier just because it fires often."""
    rng = random.Random(seed)
    patient_list = list(patients.values())
    rng.shuffle(patient_list)

    def run_tier(generators) -> list[dict]:
        pooled: list[list[dict]] = [[] for _ in generators]
        for pd in patient_list:
            for i, gen in enumerate(generators):
                pooled[i].extend(gen(pd, rng))
        per_generator = max(1, per_tier_target // len(generators))
        selected = []
        for pool in pooled:
            rng.shuffle(pool)
            selected.extend(pool[:per_generator])
        rng.shuffle(selected)
        return selected

    candidates = []
    candidates.extend(run_tier(TIER1_GENERATORS))
    candidates.extend(run_tier(TIER2_GENERATORS))
    candidates.extend(run_tier(TIER3_GENERATORS))
    return candidates
