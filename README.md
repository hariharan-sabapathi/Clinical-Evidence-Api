# clinical-evidence-api

[![CI](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml/badge.svg)](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml)

A secure backend API for retrieving patient-specific clinical evidence and
optionally generating an LLM-based answer from that evidence. A clinician
can only ever retrieve data for patients they're authorized to access,
every clinical-data access is audit-logged, and clinical text is redacted
before it's sent to an external LLM.

**Live:** https://clinical-evidence-api.onrender.com/docs

**Demo credentials** (email / password):

```
clinician.a@example.org / clinician-a-pass   (assigned to Patient A)
clinician.b@example.org / clinician-b-pass   (assigned to Patient B)
auditor@example.org     / auditor-pass       (audit log only)
admin@example.org       / admin-pass         (ingestion only, no patient data access)
```

## See the authorization model in 60 seconds

1. `POST /v1/auth/token` as clinician A, then **Authorize** in `/docs` with the token
2. `GET /v1/patients` → Patient A only
3. `POST /v1/patients/{patient_a_id}/query` → cited evidence
4. `GET /v1/patients/{patient_b_id}/documents` → 403, body leaks nothing

Step 4 is the whole project in one call: a clinician assigned to Patient A
gets a 403 — not a silent empty result, not a 500 — the moment they reach
for another clinician's patient, and the error body doesn't even leak that
patient's id.

## About the public instance

> The public instance runs in retrieval-only mode: no LLM key is
> configured on a free-tier host, so `/query` returns ranked, cited
> evidence with `"answer": null, "degraded": true`. That's the identical
> path the circuit breaker takes when a model is unavailable. Every other
> layer still runs — patient-scoped retrieval, PHI redaction before
> egress, the breaker. Set `LLM_MODEL` and `LLM_API_KEY` to enable
> generation locally.

## What this project covers

1. **Authentication and authorization** — JWT login, roles (clinician,
   auditor, admin), and an explicit per-patient assignment check.
2. **PostgreSQL + Row-Level Security** — the database itself, not just
   application code, enforces that a clinician can only read their
   assigned patients' data.
3. **Patient-scoped vector retrieval** — pgvector similarity search over
   clinical text, scoped to one patient inside the SQL query itself.
4. **FHIR ingestion** — admin uploads a FHIR bundle; it's parsed, chunked,
   embedded, and stored.
5. **Background worker** — ingestion runs asynchronously on a queue, with
   retries and job-status polling.
6. **Redis** — backs the rate limiter (and the ingestion queue).
7. **PHI redaction** — clinical text is redacted before it's sent to the LLM.
8. **LLM integration** — an interface with a null/mock implementation and
   a real provider implementation, so the API works with or without a key.
9. **Audit logging** — every clinical-data access writes an audit row, in
   the same transaction as the read.
10. **Idempotency** — retrying an ingestion request with the same key does
    not create duplicate work.
11. **Optimistic locking / ETags** — concurrent document updates are
    rejected with 409, not silently overwritten.
12. **Prometheus metrics** — request counts, latency, and errors.
13. **Testing and CI** — 130+ automated tests (including a dedicated
    `tests/security/` suite), `mypy --strict`, `ruff`, all run on every push.
14. **Deployment** — Docker Compose locally, auto-deployed from `main` in
    production.

The AI/LLM piece matters, but this is primarily a backend engineering
project: the interesting parts are the authorization model, the data
layer, the async worker, and how the service degrades gracefully when the
LLM isn't available — not the LLM call itself.

## Performance: before / after

Same k6 script (`benchmarks/k6_query_load.js`), same parameters (50 VUs,
3 minutes, a 5-question realistic mix against 60 seeded patients / 18,000
chunks) run three times, changing one thing at a time. The honest result:
the fix that was planned (an added cache layer + an HNSW index) barely
moved the number; profiling with `py-spy` found the real bottleneck was
something else entirely. Full writeup — including a Little's Law
saturation analysis and a tail-latency investigation that didn't fully
resolve, reported as such — is in
[`benchmarks/README.md`](benchmarks/README.md). **Note:** that benchmark
was run against an earlier version of this system, before semantic
caching and SSE streaming were removed for simplicity — see the note at
the top of that document.

| Metric | v0.1-naive (1 worker, no cache/index) | +cache +HNSW (1 worker) | +cache +HNSW +4 workers † |
|---|---:|---:|---:|
| avg latency | 270.79 ms | 290.30 ms | 8.57 ms |
| p95 latency | 484.34 ms | 457.47 ms | 12.47 ms |
| throughput | 97.6 req/s | 93.7 req/s | **220.1 req/s** |
| requests in flight (Little's Law, L = throughput × latency) | ≈26 | ≈27 | ≈1.9 |

† **This column is one run, not a settled number.** Two later re-runs of
the identical config, done to investigate its `max=639ms` tail, both show
a sustained elevated-latency period this run didn't — the honest range is
closer to avg 8–95ms / p95 12–380ms pending an unresolved root cause; see
[`benchmarks/README.md`](benchmarks/README.md#before--after). The 2.3×
throughput figure is also a floor, not a measured ceiling — the run never
saturated the server, so true capacity is unknown and higher.

**Read the latency drop as a saturation artifact, not a speed claim.**
~26 requests in flight under 50 offered VUs means the single-process runs
were saturated — most of that 270–290ms was queueing for one process's
attention, not work. The 4-worker run holds under 2 requests in flight:
unsaturated, latency close to pure service time. That's why throughput
(2.3× — see the footnote for why even that's a floor), not the ~30×
latency gap, is the number that describes this change. Full analysis
(why 2.3× and not 4×, the tail-latency investigation, what the flame
graph found) is in [`benchmarks/README.md`](benchmarks/README.md).

## Authorization model

**Clinicians can read only patients they're assigned to, via
`care_assignments`.** Enforcement is Postgres Row-Level Security, not an
`if` statement in a route handler:

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

The request-scoped database session sets the actor identity with
`SET LOCAL app.actor_id = :actor` (`app/db/session.py`) before any other
statement in the transaction runs. `SET LOCAL`, not `SET`: the setting is
scoped to the current transaction and is unset automatically at COMMIT or
ROLLBACK, so a connection handed back to a pool can never carry a stale
actor identity into a different request. The API and worker connect as a
`clinical_runtime` role that is **not** the schema owner and has
`FORCE ROW LEVEL SECURITY` applied against it — the owner role
(`clinical_app`) only ever runs migrations, so there's no privileged
connection path that could accidentally bypass the policy.

**Why not just an application-level check?** Two reasons, both load-
bearing:

1. **Defense in depth.** The API *also* runs an explicit
   `require_patient_assignment` check before the query — RLS is the
   backstop, not a replacement. Application-only checks are one new route
   away from a leak; RLS-only checks turn "not your patient" into a
   silent empty result instead of a 403.
2. **Scoping has to happen *inside* the query, not as a post-filter.**
   `app/services/retrieval.py`'s vector search puts `patient_id = :id` in
   the same `WHERE` clause as the `ORDER BY embedding <=> :q` — the index
   scan itself never visits another patient's rows. A retrieval system
   that runs similarity search first and filters results afterward still
   does the (wasted) work over every patient's vectors, and a single
   missed filter step leaks another patient's evidence into the answer.
   `tests/security/test_retrieval_scoping.py` proves this with a query
   engineered to match the *other* patient's text better than the
   target's own.

**Ingestion is admin-only**, not open to clinicians. The worker writes a
FHIR bundle with a service role that bypasses per-clinician RLS, so
allowing clinicians to enqueue arbitrary bundles would be a path around
the authorization model above — a clinician could otherwise POST a bundle
for *any* patient, not just their own assignments. Per-patient ingest
authorization would need the patient identity resolved synchronously
before enqueue; see Known limitations.

See `docs/adr/0001-postgres-rls-over-application-authz.md` for the full
reasoning, and `docs/adr/0005-audit-write-in-read-transaction.md` for why
every read also writes an audit row in the same transaction (auditability
over availability, deliberately).

## Security tests

**Start here:** [`tests/security/`](tests/security/) — every filename
states its own claim.

- `test_cross_patient_isolation.py` — the test that matters most. A
  clinician assigned to patient A can never read patient B's documents,
  record, or query answers, and the 403 body leaks nothing (`patient_id`
  never appears, not even in the error's `instance` field — routes are
  templated, not resolved-path, in every error response).
- `test_phi_egress.py` — proves no HIPAA Safe Harbor identifier (name,
  precise date, MRN, phone, email, SSN, street address) appears in the
  text actually sent to the LLM, captured via a scripted client double.
  > This is rule-based redaction of selected Safe Harbor identifier
  > patterns before text leaves the service boundary, with deterministic
  > pseudonyms so the model can still reason about "Patient A"
  > consistently — defense in depth, not a certified Safe Harbor
  > de-identification claim. Full de-identification needs a trained NER
  > model and a validation set.
- `test_retrieval_scoping.py` — proves the vector search itself is
  scoped in-query (see "Authorization model" above), not by
  post-filtering results.
- `test_query_audit.py` — proves `/query` writes exactly one audit row
  per call.

Also: `tests/concurrency/` (the optimistic-lock race),
`tests/unit/` (pagination cursor codec, circuit breaker),
`tests/contract/` (every non-2xx response is one consistent JSON error
shape; `/openapi.json` has an example on every endpoint), and
`tests/integration/` (real Postgres + Redis, worker crash recovery, rate
limiting, breaker degradation, retrieval-only config).

**On the `grounded` field:** `grounded: true` means the answer cites at
least one retrieved chunk, or explicitly refuses. It's a citation-presence
check, not claim-level verification that every sentence in the answer
maps to a chunk.

**On the default embedder:** `HashingEmbedder` is deterministic feature
hashing, not a learned model. It produces real vectors with real cosine
structure, so every code path — the HNSW index, the vector search itself
— is the production path. But it captures lexical overlap, not learned
semantic understanding: "heart attack" and "myocardial infarction" would
not match. `OpenAIEmbeddingClient` is implemented and is a one-line swap
in startup wiring; see `docs/adr/0006-deterministic-hashing-embedder-default.md`.

## Run locally

```bash
git clone https://github.com/hariharan-sabapathi/clinical-evidence-api && cd clinical-evidence-api
docker compose up
```

That starts Postgres (with pgvector + the two-role split bootstrapped via
`docker/init-db.sql`), Redis, runs migrations to head, then starts the API
(`:8000`) and the arq worker. Seed demo data (a couple of patients, four
users covering each role) in a second terminal:

```bash
docker compose --profile seed up seed
```

Demo logins (email / password): `clinician.a@example.org` /
`clinician-a-pass`, `auditor@example.org` / `auditor-pass`,
`admin@example.org` / `admin-pass` — see `scripts/seed_demo_data.py` for
the full list and what each is assigned to.

**Environment constraints in this deployment:** no LLM API key is
configured (same constraint the retrieval library's own README notes for
its offline evaluation) and this sandbox's network policy blocks the
Docker Hub/k6.io CDN pulls `docker compose up` and a packaged k6 binary
would normally use. Every piece of this system was still built and
verified end-to-end: Postgres 16 + pgvector and Redis installed directly
via `apt`, k6 built from source via `go install go.k6.io/k6@latest`
(the Go module proxy *is* reachable), and the full request lifecycle —
auth, RLS-scoped reads, async ingestion with a real arq worker, the
circuit breaker, the rate limiter, all three k6 runs — exercised against
those directly. `docker-compose.yml`/`Dockerfile` describe exactly that
same setup for an environment with normal registry access; nothing in
them is unverified guesswork. With `LLM_MODEL`/`LLM_API_KEY` set,
`/query` calls a real model; without one, it runs in the same
retrieval-only mode the CLI's `NullLLMClient` always has.

Once it's running, exercise the whole request lifecycle from the command line:

```bash
BASE=http://localhost:8000

# 1. Authenticate
TOKEN=$(curl -s -X POST $BASE/v1/auth/token \
  -d "username=clinician.a@example.org&password=clinician-a-pass" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)

# 2. List my assigned patients (cursor-paginated)
curl -s $BASE/v1/patients -H "Authorization: Bearer $TOKEN" | jq
PATIENT_ID=$(curl -s $BASE/v1/patients -H "Authorization: Bearer $TOKEN" | jq -r '.items[0].id')

# 3. Ask a grounded question
curl -s -X POST $BASE/v1/patients/$PATIENT_ID/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"q": "what medication is prescribed for diabetes?"}' | jq

# 4. Ingest a FHIR bundle asynchronously (admin only, idempotent)
ADMIN_TOKEN=$(curl -s -X POST $BASE/v1/auth/token \
  -d "username=admin@example.org&password=admin-pass" \
  -H "Content-Type: application/x-www-form-urlencoded" | jq -r .access_token)
curl -s -i -X POST $BASE/v1/ingest/bundles \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" -d @bundle.json
# -> 202, Location: /v1/ingest/jobs/{job_id}
```

## Known limitations

One line each — the prepared answer to "what would you improve?":

- Per-patient ingest authorization: ingestion is admin-only rather than
  resolving patient identity synchronously before enqueue
- The `/query` transaction stays open across the model call; at real
  concurrency that should be split so the DB connection is released first
- Idempotency reservation is check-then-insert, not atomic — concurrent
  identical requests can both enqueue
- RLS covers `documents`; `patients` relies on application-level scoping
- Refresh-token rotation has no concurrency guard
- Circuit breaker state is per-process and doesn't share across replicas
- `/v1/auth/token` isn't rate-limited

## What breaks at 100x

Honest answer, not a marketing one:

- **This benchmark's own corpus (18,000 chunks) wasn't large enough to
  make the vector index the bottleneck** — see "Performance" above: at
  300 chunks/patient the sequential scan was already low-single-digit
  milliseconds, which is exactly why the earlier cache+index experiment
  barely moved the number. At 100x the per-patient chunk count, that stops
  being true and the HNSW index becomes load-bearing, pushing more load
  onto retrieval + generation. The circuit breaker's retrieval-only floor
  exists precisely so generation capacity, not correctness, degrades
  gracefully first once that happens.
- **A single Postgres primary** becomes the ceiling before anything else
  does — every RLS-scoped read and every audit write goes through it.
  Read replicas would need RLS's session variable propagated per-
  connection (it's transaction-local by design, so this isn't free), and
  audit writes specifically can't move to a replica at all without
  breaking the "audit write is in the same transaction as the read" (ADR
  0005) invariant that this design otherwise leans on hard.
- **The single arq worker process** processes one bundle at a time;
  100x ingestion volume needs more worker processes (arq scales
  horizontally by running more of them against the same Redis queue —
  no code change), but also a real embedding model call at that volume,
  which the current `HashingEmbedder` sidesteps entirely (see ADR 0006).
- **The in-process circuit breaker's state doesn't share across
  instances.** Running N API replicas behind a load balancer means each
  replica opens its breaker independently — a real production version
  behind heavier load would move breaker state into Redis so the whole
  fleet degrades together instead of only the replica that happened to
  see the failures.
- **The token-bucket rate limiter is already Redis-backed and
  horizontally correct** (that's the whole reason it's a Lua script
  instead of in-process state) — this one *doesn't* need rework at 100x.

## Architecture / ADRs

**Main query flow:**

```text
Client
  ↓
FastAPI
  ↓
Authentication / Authorization
  ↓
PostgreSQL + RLS + pgvector
  ↓
Patient-scoped Retrieval
  ↓
Relevant Evidence
  ↓
PHI Redaction
  ↓
LLM
  ↓
JSON Answer + Evidence
```

**Ingestion flow:**

```text
Admin
  ↓
FHIR Upload
  ↓
Queue
  ↓
Background Worker
  ↓
Parse / Chunk / Embed
  ↓
PostgreSQL
```

**Supporting components:**

```text
Redis        → Rate Limiting
Prometheus   → API Metrics
Audit        → Clinical-data Access Records
```

Request lifecycle, ERD, dependency rationale, the zero-downtime migration
pattern, and a failure-modes table are in
[ARCHITECTURE.md](ARCHITECTURE.md). Individual design decisions and their
tradeoffs are in [`docs/adr/`](docs/adr/).

## Repository layout

```
app/                  the service (this document's subject)
  api/v1/             route handlers
  services/           auth, retrieval, generation, reliability
  models/, schemas/   SQLAlchemy models / Pydantic schemas
  workers/            arq worker + settings
  observability/      logging, metrics
alembic/versions/     migrations, including the RLS policy and the
                      three-step zero-downtime pattern (0004-0006)
tests/
  security/           start here
  concurrency/ unit/ contract/ integration/
benchmarks/           k6 script + before/after results
docs/adr/             architecture decision records
src/clinical_retrieval/   the retrieval library this service wraps
                          (its own README: README-retrieval-library.md)
```
