# 0001: Postgres Row-Level Security over application-layer authorization

## Context

Every clinical-content query in this service is scoped to "documents this
clinician is currently assigned to treat." That rule has to hold under
every code path that can read `documents` — the REST endpoints today, a
report job or an admin tool tomorrow, a future GraphQL layer, a raw psql
session during an incident. An `if not assigned: raise 403` check in each
route function is only as strong as the discipline of whoever writes the
next route.

## Decision

Enforce the scoping rule as a Postgres Row-Level Security policy on
`documents`, keyed off `current_setting('app.actor_id')`, which the
request-scoped session sets via `SET LOCAL` before any other statement
runs (see `app/db/session.py`). The API and worker connect as a
`clinical_runtime` role that is distinct from the schema owner
(`clinical_app`) and has `FORCE ROW LEVEL SECURITY` applied against it, so
the policy cannot be silently bypassed by a future migration that happens
to connect as the owner. Application code still runs an explicit
authorization check too (`require_patient_assignment`) — RLS is the
backstop, not a replacement for it, because RLS alone turns "not your
patient" into an empty result set (200) instead of the 403 an
authorization boundary should return.

## Consequences

Every new query against `documents` is safe by construction, including
ones nobody thought to unit-test — the database refuses the row, full
stop. The cost is a small amount of conceptual overhead (two places, not
one, encode "who can see what") and a `SET LOCAL` on every transaction,
which is a session-variable write, not a query, negligible next to the
query itself. Debugging "why did this query return nothing" now sometimes
means checking `care_assignments`, not just the WHERE clause — worth it
for security-relevant tables, not something we'd add everywhere.
