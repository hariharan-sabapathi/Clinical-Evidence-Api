# 0006: Deterministic feature-hashing embedder as the default, pluggable backend

## Context

Every vector-search code path (`app/services/retrieval.py`, the semantic
cache, the ingestion worker) needs an `Embedder`. A real embedding model
(OpenAI's API, or a local sentence-transformers model) is the production
answer, but this project's sandbox has no configured LLM/embeddings API
key and unreliable access to model-weight downloads (see the retrieval
library's own README note on the same constraint for its LLM client) — a
hard dependency on either would make every test, every migration
demonstration, and local `docker compose up` depend on a download or a
paid API succeeding.

## Decision

Default to `HashingEmbedder` (`app/services/embeddings.py`): a
deterministic, offline, feature-hashing vector — tokens hashed into fixed
dimensions with a sign bit, L2-normalized. It's not state-of-the-art
semantic similarity, but it is a real vector with real cosine-similarity
structure (similar text hashes to similar vectors), so pgvector's HNSW
index, the cosine-distance query in `retrieve()`, and the semantic
cache's similarity threshold all exercise the actual code path a
production embedding model would run through — nothing about the
service's architecture is mocked or stubbed for this. Both the embedder
and the LLM client sit behind the same kind of narrow protocol the
retrieval library already established for its own `LLMClient`
(`NullLLMClient` vs. `OpenAICompatibleClient`), so swapping in
`OpenAIEmbeddingClient` (implemented, unexercised here) is a one-line
change in `app/main.py`'s startup wiring.

## Consequences

Retrieval quality with `HashingEmbedder` is bag-of-words-level, not
semantic — synonyms and paraphrases won't match the way a trained model's
embedding would. That's an acceptable tradeoff for a project whose
subject is the service layer around retrieval (auth, async ingestion,
observability, reliability), not retrieval quality itself — the offline
BM25/embedding accuracy work is what `src/clinical_retrieval`'s own eval
harness already covers. Anyone deploying this for real should set
`LLM_MODEL`/an embeddings model and wire `OpenAIEmbeddingClient` in.
