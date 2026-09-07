from clinical_retrieval.evalset.paraphrase import make_paraphrases, paraphrase


def test_paraphrase_applies_synonym_substitution():
    q = "What medications was John123 prescribed during their 2020-01-01 visit?"
    result = paraphrase(q)
    assert result != q
    assert "Which drugs was John123" in result


def test_paraphrase_never_leaves_a_multi_word_name_split():
    """Regression test for the bug found during human validation (§3.4):
    an earlier structural rewrite split a multi-word patient name from the
    verb at the wrong boundary. Substitution-only must never do that since
    it only swaps fixed phrases, never touches names."""
    q = "What medications was Don899 Eliseo499 Swift555 prescribed during their 1984-07-26 visit?"
    result = paraphrase(q)
    assert "Don899 Eliseo499 Swift555" in result


def test_make_paraphrases_sets_provenance_fields():
    base = {"question": "How many times was Jane Doe seen for flu in 2020?", "answer": "2"}
    out = make_paraphrases(base, base_qid="t2_0001", n=1)
    assert len(out) == 1
    assert out[0]["generation"] == "paraphrase"
    assert out[0]["paraphrase_of"] == "t2_0001"
    assert out[0]["question"] != base["question"]


def test_make_paraphrases_returns_empty_if_no_synonym_matched():
    base = {"question": "xyz completely unmatched phrasing", "answer": "n/a"}
    assert make_paraphrases(base, base_qid="q1") == []
