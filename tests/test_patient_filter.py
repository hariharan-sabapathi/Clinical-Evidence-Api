"""Regression tests for retrieval/filters.py (§4.2's patient metadata
pre-filter).

Investigated after a report that `--patient-filter` appeared to return the
same results as an unfiltered query. Root cause: it doesn't — the top two
results happen to coincide because the named patient's own best-scoring
chunks are also the two highest-scoring chunks in the whole corpus for that
query, and BM25Okapi's IDF is (deliberately, see bm25.py) fit once over the
full corpus, so a chunk's score is identical whether or not the candidate
pool is later restricted. From rank 3 onward the filtered and unfiltered
lists diverge: unfiltered pulls in chunks from three other patients;
filtered correctly excludes all of them. These tests construct a corpus
where the wrong-patient chunk would win at every rank without the filter,
so "the filter changed nothing" would show up as a hard failure, not just a
coincidence of which chunk happens to score highest.
"""

from clinical_retrieval.common.types import Chunk
from clinical_retrieval.retrieval.bm25 import BM25Retriever
from clinical_retrieval.retrieval.filters import PatientFilteredRetriever, PatientNameResolver

PATIENT_NAMES = {
    "patient-a": "Shantelle354 Sammy219 Davis923",
    "patient-b": "Marcus771 Okafor552",
}

QUERY = "what medications was Shantelle354 on during her 2019 visits?"


def _corpus():
    return [
        # Patient A's real chunk: matches the query on the patient's name only,
        # so it scores lower than patient B's chunk below.
        Chunk(chunk_id="a1", chunk_level="full_note", patient_id="patient-a",
              text="Shantelle354 Sammy219 Davis923 visit note, unrelated content here."),
        # Patient B's chunk: happens to repeat "medications 2019 visits" many
        # times (e.g. a dense medication-reconciliation note), so an
        # unfiltered BM25 search ranks it above patient A's own chunk.
        Chunk(chunk_id="b1", chunk_level="full_note", patient_id="patient-b",
              text="medications medications 2019 visits visits medications review 2019"),
        Chunk(chunk_id="b2", chunk_level="full_note", patient_id="patient-b",
              text="another patient-b chunk, medications 2019"),
    ]


# --- 1. Name resolution ---------------------------------------------------

def test_patient_name_resolves_to_correct_patient_id():
    resolver = PatientNameResolver(PATIENT_NAMES)
    assert resolver.resolve(QUERY) == "patient-a"


def test_resolver_returns_none_when_no_name_matches():
    resolver = PatientNameResolver(PATIENT_NAMES)
    assert resolver.resolve("what medications was given during the 2019 visit?") is None


def test_resolver_returns_none_on_ambiguous_match():
    names = {"p1": "Jordan55 Smith", "p2": "Jordan55 Reyes"}
    resolver = PatientNameResolver(names)
    assert resolver.resolve("what did Jordan55 take?") is None


# --- 2/3. patient_id reaches retrieval and is applied before top-k cutoff -

def test_unfiltered_query_would_return_the_wrong_patient_first():
    """Establishes the premise: without the filter, patient B's chunks
    legitimately outscore patient A's own chunk for this query."""
    base = BM25Retriever(_corpus())
    results = base.retrieve(QUERY, k=3)
    assert results[0].chunk.patient_id == "patient-b"


def test_patient_filter_excludes_other_patients_chunks():
    base = BM25Retriever(_corpus())
    resolver = PatientNameResolver(PATIENT_NAMES)
    filtered = PatientFilteredRetriever(base, resolver, _corpus())

    results = filtered.retrieve(QUERY, k=3)

    assert len(results) == 1  # patient A has exactly one chunk in this corpus
    assert results[0].chunk.chunk_id == "a1"
    assert all(sc.chunk.patient_id == "patient-a" for sc in results)
    assert not any(sc.chunk.patient_id == "patient-b" for sc in results)


# --- 4. Candidate pool actually shrinks ------------------------------------

def test_patient_filter_reduces_candidate_pool_to_named_patient_only():
    chunks = _corpus()
    resolver = PatientNameResolver(PATIENT_NAMES)
    filtered = PatientFilteredRetriever(BM25Retriever(chunks), resolver, chunks)

    pool = filtered._chunk_ids_by_patient["patient-a"]
    assert pool == {"a1"}
    assert len(pool) < len(chunks)  # 1 < 3: the filter is a real restriction, not a no-op


# --- 5. Every returned chunk belongs to the requested patient -------------

def test_all_returned_chunks_belong_to_requested_patient():
    base = BM25Retriever(_corpus())
    resolver = PatientNameResolver(PATIENT_NAMES)
    filtered = PatientFilteredRetriever(base, resolver, _corpus())

    results = filtered.retrieve(QUERY, k=10)
    resolved_id = resolver.resolve(QUERY)
    assert results  # non-empty, otherwise the assertion below is vacuous
    assert all(sc.chunk.patient_id == resolved_id for sc in results)


def test_patient_filter_composes_with_an_existing_allowed_chunk_ids_set():
    """PatientFilteredRetriever must intersect with, not replace, an
    allowed_chunk_ids set passed in from elsewhere (e.g. a test-split
    restriction in eval/runner.py) — see filters.py's `&` on the two sets."""
    chunks = _corpus()
    resolver = PatientNameResolver(PATIENT_NAMES)
    filtered = PatientFilteredRetriever(BM25Retriever(chunks), resolver, chunks)

    results = filtered.retrieve(QUERY, k=10, allowed_chunk_ids={"a1", "b1", "b2"})
    assert [sc.chunk.chunk_id for sc in results] == ["a1"]

    results_excluding_a1 = filtered.retrieve(QUERY, k=10, allowed_chunk_ids={"b1", "b2"})
    assert results_excluding_a1 == []
