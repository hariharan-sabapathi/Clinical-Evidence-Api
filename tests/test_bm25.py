from clinical_retrieval.common.types import Chunk
from clinical_retrieval.retrieval.bm25 import BM25Retriever


def _chunks():
    return [
        Chunk(chunk_id="c1", chunk_level="full_note", patient_id="p1", text="metformin prescribed for diabetes"),
        Chunk(chunk_id="c2", chunk_level="full_note", patient_id="p2", text="completely unrelated allergy note"),
        Chunk(chunk_id="c3", chunk_level="full_note", patient_id="p1", text="diabetes follow-up, metformin continued"),
    ]


def test_bm25_ranks_relevant_chunks_above_irrelevant():
    retriever = BM25Retriever(_chunks())
    results = retriever.retrieve("metformin diabetes", k=3)
    ids = [sc.chunk.chunk_id for sc in results]
    assert ids[0] in {"c1", "c3"}
    assert ids[-1] == "c2"


def test_bm25_allowed_chunk_ids_filters_results():
    retriever = BM25Retriever(_chunks())
    results = retriever.retrieve("metformin diabetes", k=3, allowed_chunk_ids={"c2"})
    assert [sc.chunk.chunk_id for sc in results] == ["c2"]


def test_bm25_records_timing():
    retriever = BM25Retriever(_chunks())
    retriever.retrieve("metformin", k=1)
    assert "retrieve" in retriever.last_timing_ms
    assert retriever.last_timing_ms["retrieve"] >= 0
