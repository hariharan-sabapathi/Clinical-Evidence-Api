# Clinical Evidence API

[![CI](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml/badge.svg)](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml)

A backend API for retrieving patient-specific clinical evidence and optionally generating an LLM-based answer from that evidence.

The system is designed around **authorization, database-level isolation, patient-scoped retrieval, asynchronous ingestion, reliability, auditability, and graceful degradation**.

A clinician can only access patients they are assigned to. Clinical-data access is audit-logged, and clinical text is redacted before it is sent to an external LLM.

**Live API:** https://clinical-evidence-api.onrender.com/docs

---

## See the authorization model in 60 seconds

The fastest way to understand the security model is through the running API:

1. `POST /v1/auth/token` as clinician A and authorize in `/docs`
2. `GET /v1/patients` → returns only Patient A
3. `POST /v1/patients/{patient_a_id}/query` → returns patient-scoped evidence
4. `GET /v1/patients/{patient_b_id}/documents` → returns `403`

The important part is that the last request does **not** silently return an empty result or expose Patient B's data. The API explicitly rejects access to a patient outside the clinician's assignment.

---

## Public instance

The public deployment runs in **retrieval-only mode** because no LLM API key is configured on the deployment.

For `/query`, this means the service returns ranked, cited evidence with:

```json
{
  "answer": null,
  "degraded": true
}
```

This is the same degraded path used when the LLM is unavailable.

The rest of the request pipeline still runs:

```text
Authentication
    ↓
Patient authorization
    ↓
Patient-scoped retrieval
    ↓
Evidence
    ↓
PHI redaction
    ↓
LLM generation (when configured)
```

To enable generation locally, configure `LLM_MODEL` and `LLM_API_KEY`.

---

## Demo credentials

These credentials are for the seeded demo environment:

```text
clinician.a@example.org / clinician-a-pass   (assigned to Patient A)
clinician.b@example.org / clinician-b-pass   (assigned to Patient B)
auditor@example.org     / auditor-pass       (audit log access)
admin@example.org       / admin-pass         (ingestion)
```

---

## What the project covers

1. **Authentication and authorization**  
   JWT authentication, role-based access, and explicit per-patient assignment checks.

2. **PostgreSQL + Row-Level Security**  
   PostgreSQL enforces patient-data isolation at the database layer rather than relying only on application code.

3. **Patient-scoped vector retrieval**  
   pgvector similarity search is scoped to the requested patient directly in the SQL query.

4. **FHIR ingestion**  
   Admin users can submit FHIR bundles which are parsed, chunked, embedded, and stored.

5. **Asynchronous background processing**  
   Ingestion is handled by an arq worker with retries and job-status polling.

6. **Redis-backed reliability**  
   Redis is used for rate limiting and the ingestion queue.

7. **PHI redaction**  
   Clinical text is redacted before being sent to an external LLM.

8. **LLM integration**  
   The generation layer uses an interface so the service can run with a null/mock implementation or a real provider.

9. **Graceful LLM degradation**  
   If generation is unavailable, the API falls back to retrieval-only evidence rather than failing the entire request.

10. **Audit logging**  
    Clinical-data access creates an audit record in the same transaction as the read.

11. **Idempotency**  
    Ingestion requests support idempotency keys to prevent duplicate work during retries.

12. **Optimistic locking / ETags**  
    Concurrent document updates are rejected with `409` instead of silently overwriting another update.

13. **Prometheus metrics**  
    The service exposes request, latency, and error metrics.

14. **Testing and CI**  
    The project has **140 automated tests**, including dedicated security, concurrency, contract, integration, and unit tests. CI runs the test suite together with type checking and linting.

15. **Dockerized deployment**  
    The complete service can be run locally with Docker Compose and the `main` branch is used for deployment.

---

## Why this is an AI/backend project

The LLM call is only one component of the system.

The more important engineering problems are:

- How do you prevent one clinician from retrieving another patient's data?
- What happens if the LLM is unavailable?
- How do you keep ingestion asynchronous?
- How do you make retries safe?
- How do you audit clinical-data access?
- How do you prevent concurrent document updates from overwriting each other?
- How do you scope vector retrieval before results are returned?
- How does the system behave as load increases?

The project therefore focuses primarily on **backend engineering with an AI-assisted retrieval/generation layer**, rather than treating the LLM API call itself as the main feature.

---

# Authorization model

Clinicians can read only patients assigned to them through `care_assignments`.

The database enforces this using PostgreSQL Row-Level Security:

```sql
CREATE POLICY documents_select_scoped ON documents
  FOR SELECT
  USING (
    patient_id IN (
      SELECT patient_id
      FROM care_assignments
      WHERE clinician_id = current_setting('app.actor_id', true)::uuid
        AND revoked_at IS NULL
    )
  );
```

The request-scoped database session sets the actor identity using:

```sql
SET LOCAL app.actor_id = :actor
```

`SET LOCAL` keeps the identity scoped to the current transaction. When the transaction commits or rolls back, the setting disappears, preventing a pooled database connection from carrying one request's identity into another request.

The runtime API/worker role is deliberately separate from the migration role and operates with Row-Level Security enforced.

### Why both application authorization and RLS?

The application performs an explicit patient-assignment check before the query, while PostgreSQL RLS provides the database-level backstop.

This gives two layers of protection:

1. **Application authorization** produces an explicit `403` when a clinician requests another patient's data.
2. **Database RLS** prevents an accidental application-level omission from exposing another patient's rows.

Patient scoping is also performed directly inside the retrieval query rather than retrieving arbitrary vectors first and filtering them afterward.

---

# Patient-scoped retrieval

The vector search is scoped to the requested patient as part of the database query.

Conceptually:

```sql
WHERE patient_id = :patient_id
ORDER BY embedding <=> :query_embedding
```

This means the retrieval operation itself does not search across unrelated patients and then attempt to filter the results afterward.

The security test suite includes a retrieval-scoping test specifically designed to verify this behavior.

---

# Ingestion

FHIR ingestion is restricted to the admin role.

The flow is:

```text
Admin
  ↓
FHIR Bundle
  ↓
API
  ↓
Idempotency check
  ↓
Queue
  ↓
Background Worker
  ↓
Parse / Chunk / Embed
  ↓
PostgreSQL + pgvector
```

The API returns a job identifier so ingestion can be processed asynchronously rather than keeping the HTTP request open for the entire ingestion operation.

The worker handles retries and exposes job status for polling.

---

# LLM integration

The generation layer is intentionally kept behind an interface.

There is a null implementation for environments where no model API key is configured and a real provider implementation for generation.

The current public deployment uses the null/retrieval-only path.

When a real LLM is configured:

```text
Patient-scoped retrieval
        ↓
Relevant clinical evidence
        ↓
PHI redaction
        ↓
LLM
        ↓
Answer + evidence
```

The service does not send the raw retrieved clinical text directly to the external model.

### Important limitation

The PHI protection implemented here is **rule-based redaction of selected identifier patterns**. It is a defense-in-depth mechanism, not a claim of certified HIPAA Safe Harbor de-identification.

A production de-identification system would require substantially more validation, including broader entity recognition and a validation dataset.

---

# Embeddings

The default `HashingEmbedder` is deterministic feature hashing rather than a learned embedding model.

It produces actual vectors that can be stored in pgvector and queried through the same vector-retrieval path used by the service.

However, it should not be described as semantic understanding.

For example, lexical feature hashing does not guarantee that:

```text
"heart attack"
```

and:

```text
"myocardial infarction"
```

will be recognized as semantically equivalent.

A real embedding provider implementation is available behind the same embedding interface and can replace the deterministic default through configuration/startup wiring.

---

# Reliability

The service contains several mechanisms for handling failures and retries.

### Circuit breaker

The LLM generation path is protected by a circuit breaker.

When generation repeatedly fails, the service can enter a degraded state and serve retrieval-only evidence rather than repeatedly attempting an unavailable model.

### Rate limiting

The API uses a Redis-backed token-bucket rate limiter so rate-limit state can be shared across API processes.

### Idempotency

Ingestion requests accept an idempotency key so clients can safely retry requests without intentionally creating duplicate jobs.

### Optimistic locking

Document updates use optimistic concurrency control. Conflicting updates return `409` rather than silently overwriting another writer's changes.

---

# Audit logging

Clinical-data reads generate audit records.

The audit write occurs in the same transaction as the clinical read.

This deliberately prioritizes audit consistency: a successful clinical-data read should not commit while its corresponding audit record fails to persist.

---

# Security tests

Start with:

```text
tests/security/
```

Important tests include:

### `test_cross_patient_isolation.py`

Verifies that a clinician assigned to Patient A cannot retrieve Patient B's records, documents, or query results.

It also verifies that the `403` response does not expose the other patient's identifier.

### `test_phi_egress.py`

Verifies that selected clinical identifiers are removed before the text is sent to the LLM, using a scripted client double.

### `test_retrieval_scoping.py`

Verifies that patient scoping happens inside the vector retrieval query rather than as a post-filter.

### `test_query_audit.py`

Verifies that a query produces the expected audit record.

Other test groups cover:

```text
tests/concurrency/
tests/unit/
tests/contract/
tests/integration/
```

These cover areas such as optimistic-lock races, circuit-breaker behavior, pagination, API error contracts, real PostgreSQL/Redis integration, worker recovery, rate limiting, and retrieval-only degradation.

---

# Testing

The current test suite contains:

```text
140 passed
```

The suite includes:

- security tests
- integration tests
- unit tests
- concurrency tests
- API contract tests
- LLM client tests
- circuit-breaker tests
- retrieval tests
- worker tests
- database/RLS tests

CI also runs static checks such as:

```text
ruff
mypy --strict
pytest
```

---

# Performance

The repository contains a historical k6 performance experiment under:

```text
benchmarks/
```

That experiment was performed against an earlier version of the system and should **not** be interpreted as a current production performance claim.

In particular, the earlier experiment included a cache layer that has since been removed.

The benchmark remains in the repository as engineering documentation of the investigation process: it records the original measurements, the attempted optimization, the profiling work, and the conclusions about where the observed latency came from.

The important lesson from the experiment was that the initial latency improvement was primarily related to process saturation rather than simply adding a cache or vector index.

See `benchmarks/README.md` for the complete historical analysis.

---

# Run locally

Clone the repository:

```bash
git clone https://github.com/hariharan-sabapathi/Clinical-Evidence-Api
cd Clinical-Evidence-Api
```

Start the services:

```bash
docker compose up
```

This starts:

```text
PostgreSQL + pgvector
Redis
FastAPI
arq worker
```

Run the demo seed profile in another terminal:

```bash
docker compose --profile seed up seed
```

The seeded environment provides demo users for the clinician, auditor, and admin roles.

---

# Example request flow

Once the API is running:

```bash
BASE=http://localhost:8000
```

### 1. Authenticate

```bash
TOKEN=$(curl -s -X POST $BASE/v1/auth/token \
  -d "username=clinician.a@example.org&password=clinician-a-pass" \
  -H "Content-Type: application/x-www-form-urlencoded" |
  jq -r .access_token)
```

### 2. List assigned patients

```bash
curl -s $BASE/v1/patients \
  -H "Authorization: Bearer $TOKEN" |
  jq
```

### 3. Query patient evidence

```bash
PATIENT_ID=$(curl -s $BASE/v1/patients \
  -H "Authorization: Bearer $TOKEN" |
  jq -r '.items[0].id')

curl -s -X POST \
  $BASE/v1/patients/$PATIENT_ID/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"q":"what medication is prescribed for diabetes?"}' |
  jq
```

### 4. Try another patient's records

Using a patient outside the clinician's assignment should return:

```text
403 Forbidden
```

This is the simplest way to see the authorization model in action.

---

# Known limitations

The current implementation intentionally has several limitations:

- Ingestion is admin-only rather than resolving patient identity synchronously before enqueue.
- The `/query` transaction remains open across the model call; at higher concurrency this should be split so the database connection can be released before generation.
- The idempotency reservation is not fully atomic against concurrent identical requests.
- RLS currently protects the document access path; some patient-level access relies on application-level authorization.
- Refresh-token rotation does not currently include a concurrency guard.
- Circuit-breaker state is process-local and does not automatically synchronize across API replicas.
- `/v1/auth/token` is not currently rate-limited.

These are documented limitations rather than hidden gaps.

---

# What breaks at 100x?

The current architecture has clear scaling boundaries.

### PostgreSQL

PostgreSQL becomes a major bottleneck as the number of patient records, vector chunks, and audit writes grows.

Every RLS-scoped read and audit write currently goes through the primary database.

### Retrieval

The current benchmark corpus was not large enough for vector retrieval to become the dominant bottleneck.

At substantially larger per-patient chunk counts, vector indexing and retrieval become more important to overall latency.

### Background ingestion

The current deployment uses a worker process to process ingestion jobs.

At significantly higher ingestion volume, additional worker processes can consume the same queue.

### Circuit breaker

The circuit breaker is process-local.

With multiple API replicas, each replica maintains its own breaker state. A larger deployment would likely move breaker state into a shared store so the fleet can degrade consistently.

### Rate limiting

The rate limiter is already Redis-backed, so its state can be shared across API processes.

---

# Architecture

## Main query flow

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
LLM (when configured)
  ↓
JSON Answer + Evidence
```

## Ingestion flow

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

## Supporting components

```text
Redis       → Rate Limiting + Ingestion Queue
Prometheus  → API Metrics
PostgreSQL  → Application Data + RLS + pgvector
Audit       → Clinical-data Access Records
```

More detailed request lifecycle documentation, the ERD, dependency decisions, migration design, and failure modes are documented in:

```text
ARCHITECTURE.md
```

Individual architecture decisions and tradeoffs are documented in:

```text
docs/adr/
```

---

# Repository layout

```text
app/
  api/v1/              route handlers
  services/            auth, retrieval, generation, reliability
  models/, schemas/    SQLAlchemy models / Pydantic schemas
  workers/             arq worker and settings
  observability/       logging and metrics

alembic/
  versions/            database migrations and RLS policies

tests/
  security/            authorization and data-isolation tests
  concurrency/         concurrent-update tests
  unit/                unit tests
  contract/            API contract tests
  integration/         PostgreSQL/Redis integration tests

benchmarks/             k6 performance experiments
docs/adr/               architecture decision records

src/clinical_retrieval/ retrieval library used by the service
```

---

## Project focus

This project is intentionally not just an LLM wrapper.

The LLM is one component inside a larger backend system that has to deal with:

- authorization
- database isolation
- vector retrieval
- asynchronous work
- retries
- rate limiting
- concurrency
- auditing
- observability
- failure handling
- testing
- deployment

The central design goal is simple:

> **The system should remain correct and safe even when individual components — especially the LLM — are unavailable.**
