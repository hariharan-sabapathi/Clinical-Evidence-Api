"""§3.1 hand-written hard questions — natural phrasing, ambiguous referents,
synthesis required, structured differently from the tier 1/2/3 templates
rather than being the same templates with a paraphrase pass. Each is still
derived from real per-patient structured data (so answers are checkable), a
few are deliberately left ambiguous or underspecified: they're meant to
surface `Q-AMBIG` in the failure taxonomy (§7) rather than score cleanly, and
that's reported as an eval-set property, not filtered out. Authored during
the build against the real dataset rather than by a separate blinded human
reviewer — see the human-validation note in docs/eval-methodology.md for what
that means for the reported validation rate.
"""

from __future__ import annotations

import random
from collections import defaultdict

from .ground_truth import PatientData


def _d(iso: str | None) -> str:
    return iso[:10] if iso else "an unknown date"


def hw_recent_treatment(pd: PatientData) -> dict | None:
    if not pd.encounters:
        return None
    last_enc = pd.encounters[-1]
    conds = [c for c in pd.conditions if c.encounter_id == last_enc.encounter_id]
    if not conds:
        return None
    displays = sorted({c.display for c in conds})
    return {
        "tier": 0, "template_id": "hw_recent_treatment", "question_type": "hand_written_recent",
        "question": f"What's the most recent thing {pd.name} was treated for?",
        "answer": "; ".join(displays), "answer_terms": displays, "patient_id": pd.patient_id,
        "gold_encounter_ids": [last_enc.encounter_id], "gold_resource_ids": [c.condition_id for c in conds],
        "answerable": True, "generation": "hand_written",
    }


def hw_flu_shot(pd: PatientData) -> dict | None:
    flu = [i for i in pd.immunizations if "influenza" in i.display.lower()]
    if not flu:
        return None
    flu.sort(key=lambda i: i.occurrence or "")
    last = flu[-1]
    return {
        "tier": 0, "template_id": "hw_flu_shot", "question_type": "hand_written_immunization",
        "question": f"When did {pd.name} last get a flu shot?",
        "answer": f"{_d(last.occurrence)} ({last.display})", "answer_terms": [last.display],
        "patient_id": pd.patient_id,
        "gold_encounter_ids": [last.encounter_id] if last.encounter_id else [],
        "gold_resource_ids": [last.immunization_id], "answerable": True, "generation": "hand_written",
    }


def hw_condition_recurrence(pd: PatientData) -> dict | None:
    by_display: dict[str, list] = defaultdict(list)
    for c in pd.conditions:
        by_display[c.display].append(c)
    recurring = {k: v for k, v in by_display.items() if len(v) > 1}
    if not recurring:
        return None
    display, occurrences = max(recurring.items(), key=lambda kv: len(kv[1]))
    return {
        "tier": 0, "template_id": "hw_condition_recurrence", "question_type": "hand_written_recurrence",
        "question": f"Has {pd.name} been in for anything related to {display} more than once?",
        "answer": f"Yes — {len(occurrences)} times.", "answer_terms": [display], "patient_id": pd.patient_id,
        "gold_encounter_ids": sorted({c.encounter_id for c in occurrences if c.encounter_id}),
        "gold_resource_ids": [c.condition_id for c in occurrences], "answerable": True,
        "generation": "hand_written",
    }


def hw_open_allergy(pd: PatientData) -> dict | None:
    if not pd.encounters:
        return None
    # AllergyIntolerance has no encounter link — check groundedness against
    # every note, not one arbitrary reference encounter (see templates.py's
    # gen_allergy_positive/negation for the same reasoning).
    all_encs = [e.encounter_id for e in pd.encounters]
    if pd.allergies:
        substances = sorted({a.substance for a in pd.allergies})
        answer = f"Yes — documented allergies to {', '.join(substances)}."
        terms = substances
    else:
        answer = f"No documented allergies for {pd.name}."
        terms = ["No Known Allergies"]
    return {
        "tier": 0, "template_id": "hw_open_allergy", "question_type": "hand_written_allergy",
        "question": f"What allergies does {pd.name} have, if any?",
        "answer": answer, "answer_terms": terms, "patient_id": pd.patient_id,
        "gold_encounter_ids": all_encs, "gold_resource_ids": [a.allergy_id for a in pd.allergies],
        "answerable": True, "generation": "hand_written",
    }


def hw_medication_then_new_diagnosis(pd: PatientData) -> dict | None:
    for med in pd.medications:
        if not med.authored_on:
            continue
        later = [c for c in pd.conditions if c.onset and c.onset > med.authored_on]
        if later:
            first_new = later[0]
            return {
                "tier": 0, "template_id": "hw_medication_then_diagnosis",
                "question_type": "hand_written_temporal",
                "question": (
                    f"Did {pd.name} ever get put on {med.display} and then get diagnosed "
                    "with something new afterward?"
                ),
                "answer": f"Yes — {first_new.display}, diagnosed {_d(first_new.onset)}.",
                "answer_terms": [first_new.display], "patient_id": pd.patient_id,
                "gold_encounter_ids": [first_new.encounter_id] if first_new.encounter_id else [],
                "gold_resource_ids": [first_new.condition_id], "answerable": True,
                "generation": "hand_written",
            }
    return None


def hw_ambiguous_due(pd: PatientData) -> dict | None:
    """Deliberately underspecified: 'due for anything' has no structured
    definition in this corpus (no care-gap or recall resource). Kept
    unanswerable-by-design to exercise Q-AMBIG / G-REFUSE in the taxonomy."""
    if not pd.encounters:
        return None
    return {
        "tier": 0, "template_id": "hw_ambiguous_due", "question_type": "hand_written_ambiguous",
        "question": f"Is {pd.name} due for anything based on their medication history?",
        "answer": "Ambiguous — the record has no care-gap/recall data to answer this from.",
        "answer_terms": [], "patient_id": pd.patient_id,
        "gold_encounter_ids": [pd.encounters[-1].encounter_id], "gold_resource_ids": [],
        "answerable": False, "generation": "hand_written",
    }


def hw_ambiguous_vague_checkin(pd: PatientData) -> dict | None:
    """Deliberately vague: no specific condition or timeframe named."""
    if not pd.conditions or not pd.encounters:
        return None
    return {
        "tier": 0, "template_id": "hw_ambiguous_checkin", "question_type": "hand_written_ambiguous",
        "question": f"How has {pd.name} been doing lately?",
        "answer": "Ambiguous — no timeframe or dimension (a condition, a lab, overall status) is specified.",
        "answer_terms": [], "patient_id": pd.patient_id,
        "gold_encounter_ids": [pd.encounters[-1].encounter_id], "gold_resource_ids": [],
        "answerable": False, "generation": "hand_written",
    }


HAND_WRITTEN_GENERATORS = [
    hw_recent_treatment, hw_flu_shot, hw_condition_recurrence, hw_open_allergy,
    hw_medication_then_new_diagnosis, hw_ambiguous_due, hw_ambiguous_vague_checkin,
]


def generate_hand_written(patients: dict[str, PatientData], target: int, seed: int = 29) -> list[dict]:
    rng = random.Random(seed)
    patient_list = list(patients.values())
    rng.shuffle(patient_list)

    pooled: list[list[dict]] = [[] for _ in HAND_WRITTEN_GENERATORS]
    for pd in patient_list:
        for i, gen in enumerate(HAND_WRITTEN_GENERATORS):
            item = gen(pd)
            if item:
                pooled[i].append(item)

    per_generator = max(1, target // len(HAND_WRITTEN_GENERATORS))
    selected = []
    for pool in pooled:
        rng.shuffle(pool)
        selected.extend(pool[:per_generator])
    rng.shuffle(selected)
    return selected
