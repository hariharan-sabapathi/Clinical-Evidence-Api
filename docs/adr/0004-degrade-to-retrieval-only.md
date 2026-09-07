# 0004: Degrade to retrieval-only (HTTP 200) when the circuit breaker opens

## Context

The LLM call is the least reliable dependency in the request path — rate
limits, timeouts, transient provider outages. When it's failing
persistently, the circuit breaker (`app/services/circuit_breaker.py`)
opens after 5 consecutive failures so `/query` stops attempting the call
at all for a cooldown window. The question is what to hand back to the
clinician while it's open.

## Decision

Return HTTP 200 with the full ranked evidence list (real citations, real
retrieval, nothing degraded about that half), `"answer": null`, and
`"degraded": true`. Not a 503. The generated prose answer is a convenience
layered on top of the evidence — the evidence is the part a clinician can
actually act on, and retrieval doesn't depend on the LLM at all. A 503
would make a perfectly usable partial response look like a failure to any
client that branches on status code, and would throw away the evidence
along with the (unavailable) generation.

## Consequences

Clients must check the `degraded` flag rather than assuming `answer` is
always present — a documented, tested contract
(`tests/integration/test_reliability.py::test_forced_open_breaker_degrades_query_to_retrieval_only`),
not an edge case discovered in production. The tradeoff: a client that
naively checks only HTTP status will treat a degraded response as a full
success, so `degraded` has to be impossible to miss in the schema (it's a
required field on `QueryResponse`, not an optional extra).
