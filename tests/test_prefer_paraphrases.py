from clinical_retrieval.common.types import EvalQuestion
from clinical_retrieval.evalset.paraphrase import prefer_paraphrases


def _q(qid, generation="programmatic", paraphrase_of=None):
    return EvalQuestion(
        qid=qid, question=f"question {qid}", tier=1, generation=generation, answer="a",
        gold_chunk_ids=[], permissive_chunk_ids=[], gold_resource_ids=[],
        patient_id="p1", grounded_in_note=True, answerable=True, split="test",
        paraphrase_of=paraphrase_of,
    )


def test_base_question_is_dropped_when_a_paraphrase_exists():
    base = _q("t1_0001")
    para = _q("t1_0001_p1", generation="paraphrase", paraphrase_of="t1_0001")
    result = prefer_paraphrases([base, para])
    qids = {q.qid for q in result}
    assert qids == {"t1_0001_p1"}


def test_question_without_a_paraphrase_is_kept():
    base = _q("t1_0002")
    result = prefer_paraphrases([base])
    assert {q.qid for q in result} == {"t1_0002"}


def test_hand_written_questions_pass_through_unaffected():
    hw = _q("t0_0001", generation="hand_written")
    result = prefer_paraphrases([hw])
    assert {q.qid for q in result} == {"t0_0001"}


def test_mixed_set_keeps_unparaphrased_and_swaps_paraphrased():
    base1 = _q("t1_0001")
    para1 = _q("t1_0001_p1", generation="paraphrase", paraphrase_of="t1_0001")
    base2 = _q("t1_0002")  # no paraphrase
    hw = _q("t0_0001", generation="hand_written")
    result = prefer_paraphrases([base1, para1, base2, hw])
    assert {q.qid for q in result} == {"t1_0001_p1", "t1_0002", "t0_0001"}


def test_result_count_equals_input_minus_paraphrased_bases():
    base = _q("t1_0001")
    para = _q("t1_0001_p1", generation="paraphrase", paraphrase_of="t1_0001")
    other = _q("t1_0002")
    result = prefer_paraphrases([base, para, other])
    assert len(result) == 2  # 3 input - 1 dropped base
