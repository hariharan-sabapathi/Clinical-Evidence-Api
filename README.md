# Clinical Evidence API

[![CI](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml/badge.svg)](https://github.com/hariharan-sabapathi/clinical-evidence-api/actions/workflows/ci.yml)

A backend API for patient-scoped clinical data retrieval and retrieval-grounded Q&A.

The project focuses on authentication, authorization, PostgreSQL Row-Level Security, vector retrieval, asynchronous processing, Redis-based reliability, audit logging, concurrency control, testing, and graceful degradation.

**Live API:** https://clinical-evidence-api.onrender.com/docs

---

## What this project does

The API provides:

- JWT authentication
- Role-based authorization
- Patient assignment checks
- PostgreSQL Row-Level Security
- Patient-scoped vector retrieval with pgvector
- FHIR bundle ingestion
- Asynchronous background processing
- Redis-backed rate limiting
- Ingestion idempotency
- Optimistic locking with `409` conflicts
- Clinical-data audit logging
- PHI redaction before external generation
- Retrieval-grounded Q&A
- Retrieval-only fallback when generation is unavailable
- Prometheus metrics
- Docker-based local deployment

---

## LLM integration status

The LLM generation layer is currently being implemented and tested.

The API already supports the retrieval and Q&A pipeline, including patient-scoped retrieval, evidence citations, PHI redaction, and graceful fallback to retrieval-only results.

LLM provider integration is still under testing and is not yet considered finalized.

---

## Authorization

Clinicians can only access patients assigned to them.

There are two layers of protection:

1. The API checks the clinician's patient assignment and returns `403 Forbidden` when access is not allowed.
2. PostgreSQL Row-Level Security provides database-level protection against accidental cross-patient access.

The request-scoped database session sets the authenticated actor:

```sql
SET LOCAL app.actor_id = :actor
```

The database then uses that identity when evaluating RLS policies.

Patient scoping is also applied directly inside retrieval queries rather than retrieving unrelated patients first and filtering afterward.

---

## Query API

The main Q&A endpoint is:

```text
POST /v1/patients/{patient_id}/query
```

Example request:

```json
{
  "q": "What medications was the patient on during 2019 visits?",
  "top_k": 5
}
```

The request goes through:

```text
Authentication
      ↓
Patient authorization
      ↓
Patient-scoped vector retrieval
      ↓
Relevant clinical evidence
      ↓
Answer generation
      ↓
Answer + citations
```

The response includes information such as:

```text
question
answer
citations
grounded
degraded
served_by
retrieval_ms
generation_ms
```

When answer generation is unavailable, the API can return the retrieved evidence instead of failing the entire request.

Example degraded response:

```json
{
  "answer": null,
  "citations": [],
  "grounded": null,
  "degraded": true,
  "served_by": "retrieval_only"
}
```

This makes the retrieval layer independently useful and allows the API to degrade gracefully when generation is unavailable.

---

## Ingestion

FHIR ingestion is restricted to the admin role.

The ingestion flow is asynchronous:

```text
Admin
  ↓
FHIR Bundle
  ↓
API
  ↓
Idempotency Check
  ↓
Queue
  ↓
Background Worker
  ↓
Parse / Chunk / Embed
  ↓
PostgreSQL + pgvector
```

The API returns a job identifier instead of keeping the HTTP request open while the entire ingestion process runs.

The worker handles retries and exposes job status for polling.

---

## Vector retrieval

Clinical documents are split into chunks and stored with vector representations in PostgreSQL using pgvector.

Retrieval is scoped to the requested patient:

```sql
WHERE patient_id = :patient_id
ORDER BY embedding <=> :query_embedding
```

The patient restriction is therefore part of the retrieval query itself.

The default embedding implementation uses deterministic feature hashing. It produces vectors suitable for the pgvector retrieval pipeline, but it is not presented as a learned semantic embedding model.

---

## Reliability

### Circuit breaker

The generation path is protected by a circuit breaker.

Repeated generation failures can cause the service to enter a degraded state and serve retrieval-only evidence instead of repeatedly calling an unavailable dependency.

### Rate limiting

Redis provides a token-bucket rate limiter so rate-limit state can be shared across API processes.

### Idempotency

Ingestion requests support idempotency keys so clients can safely retry requests without intentionally creating duplicate jobs.

### Optimistic locking

Concurrent document updates use optimistic concurrency control.

Conflicting updates return:

```text
409 Conflict
```

instead of silently overwriting another update.

---

## Audit logging

Clinical-data reads create audit records.

The audit write occurs in the same transaction as the clinical read.

This means a successful clinical-data read is not committed without its corresponding audit record being persisted.

---

## PHI handling

The generation pipeline includes rule-based PHI redaction before clinical text is sent to an external model.

The redaction implementation is a defense-in-depth mechanism.

It should **not** be interpreted as certified HIPAA Safe Harbor de-identification.

A production de-identification system would require broader entity detection, validation, and testing.

---

## Security tests

Security-related tests are located under:

```text
tests/security/
```

Important tests include:

### Cross-patient isolation

`test_cross_patient_isolation.py`

Verifies that a clinician assigned to Patient A cannot access Patient B's records, documents, or query results.

It also verifies that unauthorized responses do not expose the other patient's identifier.

### Retrieval scoping

`test_retrieval_scoping.py`

Verifies that patient scoping happens inside the vector retrieval query rather than as a post-filter.

### PHI egress

`test_phi_egress.py`

Verifies that selected clinical identifiers are removed before text is sent to the generation layer.

### Query auditing

`test_query_audit.py`

Verifies that patient queries create the expected audit records.

---

## Testing

The current test suite contains:

```text
140 passed
```

The tests cover:

- security
- authorization
- PostgreSQL and RLS
- Redis integration
- vector retrieval
- API contracts
- concurrency
- optimistic locking
- worker behavior
- rate limiting
- circuit-breaker behavior
- generation clients
- retrieval-only degradation
- unit-level service behavior

CI runs checks including:

```text
ruff
mypy --strict
pytest
```

---

## Run locally

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

To load the seeded demo environment:

```bash
docker compose --profile seed up seed
```

---

## Demo credentials

The seeded environment provides these demo users:

```text
clinician.a@example.org / clinician-a-pass
clinician.b@example.org / clinician-b-pass
auditor@example.org     / auditor-pass
admin@example.org       / admin-pass
```

Roles:

```text
Clinician → assigned patient access
Auditor   → audit log access
Admin     → ingestion
```

These credentials are only for the demo environment.

---

## Example API flow

Once the API is running:

```bash
BASE=http://localhost:8000
```

### 1. Authenticate

```bash
TOKEN=$(curl -s -X POST $BASE/v1/auth/token   -d "username=clinician.a@example.org&password=clinician-a-pass"   -H "Content-Type: application/x-www-form-urlencoded" |
  jq -r .access_token)
```

### 2. List assigned patients

```bash
curl -s $BASE/v1/patients   -H "Authorization: Bearer $TOKEN" |
  jq
```

### 3. Query patient evidence

```bash
PATIENT_ID=$(curl -s $BASE/v1/patients   -H "Authorization: Bearer $TOKEN" |
  jq -r '.items[0].id')

curl -s -X POST   $BASE/v1/patients/$PATIENT_ID/query   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"q":"what medication is prescribed for diabetes?"}' |
  jq
```

### 4. Try another patient's records

Requesting a patient outside the clinician's assignment should return:

```text
403 Forbidden
```

This provides a quick demonstration of the authorization model.

---

## Architecture

### Query flow

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
Generation Layer
  ↓
JSON Answer + Citations
```

### Ingestion flow

```text
Admin
  ↓
FHIR Upload
  ↓
API
  ↓
Queue
  ↓
Background Worker
  ↓
Parse / Chunk / Embed
  ↓
PostgreSQL
```

### Supporting components

```text
PostgreSQL → Application data + RLS + pgvector
Redis      → Rate limiting + ingestion queue
Prometheus → API metrics
Worker     → Asynchronous ingestion
Audit      → Clinical-data access records
```

More detailed architecture documentation is available in:

```text
ARCHITECTURE.md
```

Architecture decisions are documented in:

```text
docs/adr/
```

---

## Repository structure

```text
app/
  api/v1/              API route handlers
  services/            authentication, retrieval, generation, reliability
  models/, schemas/    SQLAlchemy models / Pydantic schemas
  workers/             background worker and settings
  observability/       logging and metrics

alembic/
  versions/            database migrations and RLS policies

tests/
  security/            authorization and data-isolation tests
  concurrency/         concurrent-update tests
  unit/                unit tests
  contract/            API contract tests
  integration/         PostgreSQL/Redis integration tests

benchmarks/             performance experiments
docs/adr/               architecture decision records
src/clinical_retrieval/
                        retrieval library used by the service
```

---

## Known limitations

The current implementation has several documented limitations:

- Some patient-level access still relies on application-level authorization in addition to RLS.
- The `/query` transaction remains open during answer generation.
- The idempotency reservation is not fully atomic against concurrent identical requests.
- Refresh-token rotation does not currently include a concurrency guard.
- Circuit-breaker state is process-local.
- `/v1/auth/token` is not currently rate-limited.

These are documented design limitations rather than hidden gaps.

---

## Project focus

This project is intentionally more than an LLM wrapper.

The main engineering focus is building a backend that remains controlled and predictable when individual components fail.

The system combines:

- authentication
- authorization
- database isolation
- vector retrieval
- asynchronous processing
- retries
- rate limiting
- concurrency control
- audit logging
- observability
- failure handling
- automated testing
- Docker-based deployment

The central design goal is:

> **The system should remain correct and safe even when individual components are unavailable.**
