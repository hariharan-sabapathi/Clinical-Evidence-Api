# Clinical Evidence API

[![CI](https://github.com/hariharan-sabapathi/Clinical-Evidence-Api/actions/workflows/ci.yml/badge.svg)](https://github.com/hariharan-sabapathi/Clinical-Evidence-Api/actions/workflows/ci.yml)

A backend API for patient-scoped clinical data retrieval and retrieval-grounded Q&A.

Per-clinician authorization enforced by PostgreSQL Row-Level Security, append-only audit logging in the same transaction as the read it records, asynchronous FHIR bundle ingestion with idempotency, and a generation path that degrades to retrieval-only rather than failing when its dependency is unavailable.

**Live API:** https://clinical-evidence-api.onrender.com/docs

The retrieval logic underneath this service is the existing BM25/vector evaluation work in [`src/clinical_retrieval`](README-retrieval-library.md), wired in rather than reimplemented. This repository is about the service *around* that logic.

**Start here:** [Authorization](#authorization) · [Performance](#performance) · [`tests/security/`](tests/security) · [ARCHITECTURE.md](ARCHITECTURE.md) · [`docs/adr/`](docs/adr)

---

## What this project does

- JWT authentication and role-based authorization
- Patient assignment checks, backstopped by PostgreSQL Row-Level Security
- Patient-scoped vector retrieval with pgvector
- Asynchronous FHIR bundle ingestion with idempotency keys and job polling
- Redis-backed token-bucket rate limiting
- Optimistic locking with `409` conflicts
- Append-only clinical-data audit logging
- Rule-based PHI redaction before external generation
- Retrieval-only fallback when generation is unavailable
- Prometheus metrics, structured logs with request IDs
- Docker-based local deployment, Alembic migrations including a three-step zero-downtime pattern

---

## Authorization

Clinicians can only access patients assigned to them, via `care_assignments`.

Enforcement is Row-Level Security in Postgres, not an `if` statement in a route handler:

```sql
CREATE POLICY documents_select_scoped ON documents
  FOR SELECT
  USING (
    patient_id IN (
      SELECT patient_id FROM care_assignments
      WHERE clinician_id = current_setting('app.actor_id', true)::uuid
        AND revoked_at IS NULL
    )
  );
```

The request-scoped session sets the actor identity before any other statement in the transaction runs (`app/db/session.py`):

```sql
SET LOCAL app.actor_id = :actor
```

`SET LOCAL`, not `SET` — the setting is scoped to the current transaction and is unset at COMMIT or ROLLBACK, so a connection handed back to the pool can never carry a stale actor identity into a different request. In code this is `set_config('app.actor_id', :actor, true)`, the parameterizable equivalent: bare `SET` doesn't accept bind parameters, and building it by string interpolation would reopen the injection hole RLS exists to close.

Token issuance opens a transaction but skips the `SET LOCAL` entirely. RLS policies then evaluate against NULL, every predicate is false, and an unauthenticated connection fails closed by default.

The API and worker connect as a `clinical_runtime` role that is **not** the schema owner and has `FORCE ROW LEVEL SECURITY` applied against it. The owner role (`clinical_app`) only runs migrations, so there is no privileged connection path that could accidentally bypass the policy.

**Why not just an application-level check?** Two reasons:

1. **Defense in depth.** The API also runs an explicit assignment check before the query and returns `403`. RLS is the backstop, not the replacement — an application-only check is one new route away from a leak.
2. **Scoping has to happen inside the query, not as a post-filter.** See [Vector retrieval](#vector-retrieval).

Full reasoning: [`docs/adr/0001-postgres-rls-over-application-authz.md`](docs/adr/0001-postgres-rls-over-application-authz.md).

---

## Vector retrieval

Clinical documents are chunked and stored with vector representations in Postgres via pgvector. The patient restriction is part of the retrieval query itself:

```sql
WHERE patient_id = :patient_id
ORDER BY embedding <=> :query_embedding
```

The filter is in the same `WHERE` clause as the ordering, so the index scan never visits another patient's rows. A system that runs similarity search first and filters afterward does the wasted work over every patient's vectors, and a single missed filter step leaks another patient's evidence into the answer. [`tests/security/test_retrieval_scoping.py`](tests/security/test_retrieval_scoping.py) proves this with a query engineered to match the *other* patient's text better than the target's own.

The default embedding implementation is deterministic feature hashing. It produces vectors suitable for the pgvector pipeline and makes tests reproducible without a model download; it is not presented as a learned semantic embedding model. Reasoning: [`docs/adr/0006-deterministic-hashing-embedder-default.md`](docs/adr/0006-deterministic-hashing-embedder-default.md).

---

## Performance

**Scope: this measures the service layer, not end-to-end answer latency.** These runs used the retrieval-only path — no `LLM_MODEL` configured, so generation is the null client and contributes no time. That is deliberate. A provider call is I/O-bound await worth hundreds of milliseconds; including it would dominate every percentile below and bury the thing being measured. What the numbers describe is auth, RLS-scoped retrieval, and serialization — the part of the request the service is responsible for. Read them as service overhead, not as what a clinician would experience.

Same k6 script ([`benchmarks/k6_query_load.js`](benchmarks/k6_query_load.js)), same parameters — 50 VUs, 3 minutes, a 5-question mix against 60 seeded patients / 18,000 chunks — run three times, changing one thing at a time.

| Metric (generation excluded) | v0.1-naive (1 worker) | +cache +HNSW (1 worker) | +cache +HNSW +4 workers † |
|---|---:|---:|---:|
| avg service latency | 270.79 ms | 290.30 ms | 8.57 ms |
| p95 service latency | 484.34 ms | 457.47 ms | 12.47 ms |
| throughput | 97.6 req/s | 93.7 req/s | **220.1 req/s** |
| requests in flight (L = λW) | ≈26 | ≈27 | ≈1.9 |
| error rate | 0.00% | 0.00% | 0.00% |

**The number to read is throughput (2.3×), not the ~30× latency gap.** At ≈26 requests in flight under 50 offered VUs, the single-process runs were saturated — most of that 270–290 ms was queueing for one process's attention, not work. The 4-worker run holds under 2 in flight. A saturated-vs-unsaturated latency comparison always looks dramatic and isn't a claim about per-request work getting faster.

**The planned fix was the wrong fix.** The middle column is a semantic cache plus an HNSW index, and it barely moved the number. Profiling with `py-spy` ([`benchmarks/flamegraph.svg`](benchmarks/flamegraph.svg)) showed the bottleneck was per-request framework and session overhead serialized through one Python process — at 300 chunks per patient, retrieval was never the constraint. Process-level concurrency is what relieved it. The cache was subsequently removed rather than carried as unused complexity.

That finding survives adding a real provider. The overhead the flame graph found is CPU-bound work serialized by the GIL, which an `await` on a network call does not relieve — more worker processes remain the fix. What changes is the reading: with generation in the path, the provider dominates the percentiles and this overhead becomes a small fraction of end-to-end time.

† **This column is one run, not a settled number.** Two re-runs of the identical configuration, done to investigate its `max=639 ms` tail, both showed a sustained elevated-latency period this run did not — the honest range is closer to avg 8–95 ms / p95 12–380 ms, root cause unresolved. The 2.3× figure is also a floor: the run never saturated the server, so true capacity is unknown and higher.

Full writeup, including the Little's Law saturation analysis, the [why-not-4× investigation](benchmarks/README.md#why-23-not-4) with live Postgres and Redis metrics, and the [unresolved tail-latency work](benchmarks/README.md#tail-latency-investigated-not-fully-resolved): [`benchmarks/README.md`](benchmarks/README.md).

> These runs predate a later simplification pass that removed semantic caching and SSE streaming from the service. `SEMANTIC_CACHE_ENABLED` and the cache-stampede lock referenced in the benchmark writeup no longer exist in the code. The measurements are unchanged and still describe what was run.

---

## What breaks at 100×

Reasoned from the architecture and the measurements above, not from a 100× load test — these are the ceilings this design would hit first, in the order it would hit them.

- **The vector index becomes load-bearing.** At this corpus size the sequential scan was already low-single-digit milliseconds, which is why indexing it didn't help. At 100× the per-patient chunk count that stops being true.
- **A single Postgres primary is the ceiling.** Every RLS-scoped read and every audit write goes through it. Read replicas would need the session variable propagated per connection — it's transaction-local by design, so this isn't free — and audit writes can't move to a replica at all without breaking the same-transaction invariant in ADR 0005.
- **The single arq worker processes one bundle at a time.** arq scales horizontally against the same Redis queue with no code change, but 100× ingestion volume also needs a real embedding model call, which the hashing embedder sidesteps.
- **Circuit-breaker state is process-local.** N API replicas each open their breaker independently. A production version would move breaker state into Redis so the fleet degrades together.
- **The rate limiter already scales.** It's a Redis Lua script rather than in-process state, so this one doesn't need rework.

---

## Query API

```text
POST /v1/patients/{patient_id}/query
```

```json
{
  "q": "What medications was the patient on during 2019 visits?",
  "top_k": 5
}
```

Request path: authentication → patient authorization → patient-scoped vector retrieval → evidence → generation → answer with citations.

The response carries `question`, `answer`, `citations`, `grounded`, `degraded`, `served_by`, `retrieval_ms`, and `generation_ms`.

### Generation and graceful degradation

The generation layer is designed to tolerate an absent or failing provider rather than depend on one. With `LLM_MODEL` and `LLM_API_KEY` set, `/query` calls a real model. Without them — or when the circuit breaker is open after repeated failures — the endpoint returns the retrieved evidence instead of failing the request:

```json
{
  "answer": null,
  "citations": [],
  "grounded": null,
  "degraded": true,
  "served_by": "retrieval_only"
}
```

This is a deliberate design choice, not a fallback bolted on: the retrieval layer is independently useful, and `degraded` / `served_by` make the mode explicit to the caller rather than silent. Reasoning: [`docs/adr/0004-degrade-to-retrieval-only.md`](docs/adr/0004-degrade-to-retrieval-only.md). The deployed demo runs in retrieval-only mode, since no provider key is configured for it.

---

## Ingestion

FHIR ingestion is restricted to the admin role and is asynchronous — the API returns a job identifier rather than holding the HTTP request open for the full parse/chunk/embed cycle.

```text
Admin → FHIR bundle → API → idempotency check → arq queue
      → worker → parse / chunk / embed → Postgres + pgvector
```

The worker handles retries and exposes job status for polling. Clients pass an `Idempotency-Key` header so a retried request doesn't create a duplicate job. arq over Celery: [`docs/adr/0003-arq-over-celery.md`](docs/adr/0003-arq-over-celery.md).

---

## Reliability

**Circuit breaker.** Repeated generation failures open the breaker and put the service into the degraded state above, rather than repeatedly calling a dependency that is known to be down.

**Rate limiting.** A Redis token bucket implemented as a Lua script, so limit state is shared across API processes rather than held per-process.

**Idempotency.** Ingestion requests accept idempotency keys so clients can retry safely.

**Optimistic locking.** Concurrent document updates return `409 Conflict` instead of silently overwriting.

---

## Audit logging

Clinical-data reads write audit records, and the audit write happens in the same transaction as the read it records — a successful read cannot commit without its audit row.

This trades availability for auditability on purpose: if the audit write fails, the read fails. In a system handling PHI that is the correct direction to fail. Reasoning: [`docs/adr/0005-audit-write-in-read-transaction.md`](docs/adr/0005-audit-write-in-read-transaction.md), which also covers the cost — see [Known limitations](#known-limitations).

---

## PHI handling

The generation pipeline applies rule-based PHI redaction before clinical text is sent to an external model.

This is a defense-in-depth mechanism and should **not** be read as certified HIPAA Safe Harbor de-identification. A production de-identification system would need broader entity detection, validation, and its own test corpus.

---

## Security tests

Every filename in [`tests/security/`](tests/security) states its own claim.

| Test | Claim |
|---|---|
| [`test_cross_patient_isolation.py`](tests/security/test_cross_patient_isolation.py) | A clinician assigned to patient A can never read patient B's record, documents, or query answers — and the 403 body leaks nothing, including in the error's `instance` field |
| [`test_retrieval_scoping.py`](tests/security/test_retrieval_scoping.py) | The vector search is scoped in-query, not by post-filtering results |
| [`test_phi_egress.py`](tests/security/test_phi_egress.py) | Selected clinical identifiers are removed before text reaches the generation layer, verified via a scripted client double |
| [`test_query_audit.py`](tests/security/test_query_audit.py) | Patient queries create the expected audit records |

Also: [`tests/concurrency/`](tests/concurrency) (optimistic-lock race), [`tests/contract/`](tests/contract) (every non-2xx response is one RFC 7807 shape; `/openapi.json` has an example on every endpoint), [`tests/integration/`](tests/integration) (real Postgres and Redis, worker crash recovery, rate limiting, breaker degradation), [`tests/unit/`](tests/unit).

---

## Testing

140 tests, covering authorization and RLS, Redis integration, vector retrieval, API contracts, concurrency and optimistic locking, worker behavior, rate limiting, circuit-breaker behavior, generation clients, retrieval-only degradation, and unit-level service behavior.

CI runs `ruff`, `mypy --strict` on `app/` (no `type: ignore` suppressions), and `pytest` against real Postgres and Redis service containers. The migrations job runs a full `upgrade head → downgrade -1 → upgrade head` cycle, so every migration's `downgrade` is exercised rather than assumed.

---

## Run locally

```bash
git clone https://github.com/hariharan-sabapathi/Clinical-Evidence-Api
cd Clinical-Evidence-Api
docker compose up
```

That starts Postgres with pgvector (the two-role split bootstrapped via `docker/init-db.sql`), Redis, runs migrations to head, then starts the API on `:8000` and the arq worker. Seed demo data in a second terminal:

```bash
docker compose --profile seed up seed
```

### Demo credentials

```text
clinician.a@example.org / clinician-a-pass    assigned patient access
clinician.b@example.org / clinician-b-pass    assigned patient access
auditor@example.org     / auditor-pass        audit log access
admin@example.org       / admin-pass          ingestion
```

Demo environment only.

### Example flow

```bash
BASE=http://localhost:8000

# 1. Authenticate
TOKEN=$(curl -s -X POST $BASE/v1/auth/token \
  -d "username=clinician.a@example.org&password=clinician-a-pass" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)

# 2. List assigned patients (cursor-paginated)
curl -s $BASE/v1/patients -H "Authorization: Bearer $TOKEN" | jq
PATIENT_ID=$(curl -s $BASE/v1/patients -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].id')

# 3. Ask a grounded question
curl -s -X POST $BASE/v1/patients/$PATIENT_ID/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"q":"what medication is prescribed for diabetes?"}' | jq

# 4. Request a patient outside the assignment -> 403, and the response
#    body contains no identifier for the patient that was requested
```

---

## Architecture

```text
Client → FastAPI → auth / authorization → Postgres (RLS + pgvector)
       → patient-scoped retrieval → evidence → generation → JSON + citations

Postgres  application data, RLS policies, pgvector
Redis     rate limiting, ingestion queue, idempotency
Worker    asynchronous ingestion (arq)
Audit     append-only clinical-data access records
```

Request lifecycle, ERD, and the three-step zero-downtime migration pattern are in [ARCHITECTURE.md](ARCHITECTURE.md). Design decisions are in [`docs/adr/`](docs/adr):

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-postgres-rls-over-application-authz.md) | Postgres RLS over application-level authorization |
| [0002](docs/adr/0002-cursor-over-offset-pagination.md) | Cursor over offset pagination |
| [0003](docs/adr/0003-arq-over-celery.md) | arq over Celery |
| [0004](docs/adr/0004-degrade-to-retrieval-only.md) | Degrade to retrieval-only |
| [0005](docs/adr/0005-audit-write-in-read-transaction.md) | Audit write inside the read transaction |
| [0006](docs/adr/0006-deterministic-hashing-embedder-default.md) | Deterministic hashing embedder as the default |

---

## Repository structure

```text
app/
  api/v1/              route handlers
  services/            auth, retrieval, generation, reliability
  models/, schemas/    SQLAlchemy models / Pydantic schemas
  workers/             arq worker and settings
  observability/       logging and metrics
alembic/versions/      migrations, including the RLS policy (0002) and the
                       three-step zero-downtime pattern (0004-0006)
tests/                 security/ concurrency/ contract/ integration/ unit/
benchmarks/            k6 script, raw run output, flamegraph, analysis
docs/adr/              architecture decision records
src/clinical_retrieval/    the retrieval library this service wraps
```

---

## Known limitations

**Deliberate tradeoffs.** These are design decisions with a documented cost:

- Patient-level access uses an application check *in addition to* RLS. RLS alone turns "not your patient" into a silent empty result rather than a `403`; the application check exists to produce the right status code, with RLS as the backstop.
- The `/query` transaction stays open across answer generation, because the audit write and the read must share a transaction (ADR 0005). The cost is a held Postgres connection for the duration of a model call — the constraint that limits concurrency first under real load.
- The default embedder is deterministic feature hashing rather than a learned model (ADR 0006).

**Open items.** These are defects, not choices, and are not yet fixed:

- `/v1/auth/token` is not rate-limited. The Redis token bucket exists and is not yet applied to the one unauthenticated endpoint.
- The idempotency reservation is not fully atomic against concurrent identical requests, so a simultaneous duplicate can still produce two jobs.
- Refresh-token rotation has no concurrency guard.
- Circuit-breaker state is process-local and does not share across replicas.

---

## Project focus

The engineering goal is a backend that stays correct and predictable when individual components fail — where authorization is enforced by the database rather than trusted to route handlers, every clinical read is accountable, and an unavailable model degrades the answer instead of the service.
