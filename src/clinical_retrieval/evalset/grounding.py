"""§3.2 groundedness filter. A fact computed from structured FHIR (e.g. a lab
value) may never be restated in narrative note text — this checks each
candidate's `answer_terms` against its gold encounter's note text and labels
it grounded / not grounded rather than silently discarding the ungrounded
ones. Report the grounding rate per tier (docs/eval-methodology.md); an
uneven rate across tiers is itself a finding about clinical documentation,
not a bug to fix away.
"""

from __future__ import annotations

import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def is_grounded(candidate: dict, note_text_by_encounter: dict[str, str]) -> bool:
    terms = candidate.get("answer_terms") or []
    if not terms:
        return False
    gold_encounters = candidate.get("gold_encounter_ids") or []
    combined = normalize(
        " ".join(note_text_by_encounter.get(eid, "") for eid in gold_encounters)
    )
    if not combined:
        return False
    return all(normalize(term) in combined for term in terms)


def annotate_grounding(candidates: list[dict], note_text_by_encounter: dict[str, str]) -> list[dict]:
    for c in candidates:
        c["grounded_in_note"] = is_grounded(c, note_text_by_encounter)
    return candidates


def grounding_report(candidates: list[dict]) -> dict[str, dict]:
    """Grounding rate broken down by tier and by question_type."""
    report: dict[str, dict] = {}
    for key_fn, label in [(lambda c: f"tier_{c['tier']}", "by_tier"), (lambda c: c["question_type"], "by_type")]:
        counts: dict[str, list[int, int]] = {}
        for c in candidates:
            k = key_fn(c)
            counts.setdefault(k, [0, 0])
            counts[k][1] += 1
            if c.get("grounded_in_note"):
                counts[k][0] += 1
        report[label] = {k: {"grounded": g, "total": t, "rate": round(g / t, 3) if t else 0.0} for k, (g, t) in counts.items()}
    return report
