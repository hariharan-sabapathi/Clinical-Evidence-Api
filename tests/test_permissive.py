from clinical_retrieval.common.types import Chunk
from clinical_retrieval.evalset.permissive import (
    build_encounter_to_chunk_ids,
    chunks_by_patient,
    compute_permissive_chunk_ids,
)


def _chunk(chunk_id, patient_id, text, encounter_id=None, level="full_note", source_ids=None):
    if source_ids is None:
        source_ids = (encounter_id,) if encounter_id else ()
    return Chunk(
        chunk_id=chunk_id, chunk_level=level, patient_id=patient_id, text=text,
        encounter_id=encounter_id, source_resource_ids=tuple(source_ids),
    )


def test_permissive_finds_all_same_patient_chunks_containing_term():
    chunks = [
        _chunk("c1", "p1", "metformin was prescribed", "e1"),
        _chunk("c2", "p1", "no relevant content", "e2"),
        _chunk("c3", "p1", "later note also mentions metformin", "e3"),
        _chunk("c4", "p2", "metformin here too but wrong patient", "e4"),
    ]
    by_patient = chunks_by_patient(chunks)
    result = compute_permissive_chunk_ids(["metformin"], "p1", by_patient)
    assert set(result) == {"c1", "c3"}


def test_permissive_is_case_insensitive():
    chunks = [_chunk("c1", "p1", "Metformin Hydrochloride", "e1")]
    by_patient = chunks_by_patient(chunks)
    result = compute_permissive_chunk_ids(["metformin"], "p1", by_patient)
    assert result == ["c1"]


def test_permissive_empty_terms_returns_empty():
    chunks = [_chunk("c1", "p1", "anything", "e1")]
    by_patient = chunks_by_patient(chunks)
    assert compute_permissive_chunk_ids([], "p1", by_patient) == []


def test_build_encounter_to_chunk_ids_full_note():
    chunks = [_chunk("c1", "p1", "text", "e1"), _chunk("c2", "p1", "text", "e2")]
    mapping = build_encounter_to_chunk_ids(chunks)
    assert mapping == {"e1": ["c1"], "e2": ["c2"]}


def test_build_encounter_to_chunk_ids_fixed_512_multi_encounter_window():
    chunk = _chunk("w1", "p1", "text", encounter_id="e1", level="fixed_512", source_ids=["e1", "e2"])
    mapping = build_encounter_to_chunk_ids([chunk])
    assert mapping == {"e1": ["w1"], "e2": ["w1"]}
