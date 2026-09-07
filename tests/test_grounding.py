from clinical_retrieval.evalset.grounding import grounding_report, is_grounded


def test_is_grounded_true_when_all_terms_present():
    candidate = {"answer_terms": ["Metformin", "Insulin"], "gold_encounter_ids": ["e1"]}
    notes = {"e1": "Patient was given Metformin and Insulin at this visit."}
    assert is_grounded(candidate, notes) is True


def test_is_grounded_false_when_a_term_is_missing():
    candidate = {"answer_terms": ["Metformin", "Nonexistent Drug"], "gold_encounter_ids": ["e1"]}
    notes = {"e1": "Patient was given Metformin."}
    assert is_grounded(candidate, notes) is False


def test_is_grounded_false_with_no_answer_terms():
    candidate = {"answer_terms": [], "gold_encounter_ids": ["e1"]}
    notes = {"e1": "anything"}
    assert is_grounded(candidate, notes) is False


def test_is_grounded_checks_across_multiple_gold_encounters():
    candidate = {"answer_terms": ["Metformin"], "gold_encounter_ids": ["e1", "e2"]}
    notes = {"e1": "nothing relevant", "e2": "Metformin prescribed here"}
    assert is_grounded(candidate, notes) is True


def test_grounding_report_rates():
    candidates = [
        {"tier": 1, "question_type": "a", "grounded_in_note": True},
        {"tier": 1, "question_type": "a", "grounded_in_note": False},
        {"tier": 2, "question_type": "b", "grounded_in_note": True},
    ]
    report = grounding_report(candidates)
    assert report["by_tier"]["tier_1"] == {"grounded": 1, "total": 2, "rate": 0.5}
    assert report["by_tier"]["tier_2"] == {"grounded": 1, "total": 1, "rate": 1.0}
    assert report["by_type"]["a"]["rate"] == 0.5
