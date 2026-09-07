# 0005: Write the audit event in the same transaction as the read it records

## Context

Every read of clinical content is legally/operationally required to leave
an audit trail: who, when, what resource, why (purpose of use). The
question is what happens if the audit write itself fails (disk full,
constraint violation, connection drop mid-transaction) — does the
clinical read succeed anyway, or does the whole request fail?

## Decision

The audit row is written (`app/services/audit.py::record_access`) and
flushed inside the *same* database transaction as the read it documents,
using the same request-scoped session. If the audit write fails, the
transaction rolls back and the read fails with it — the client gets a
500, not the clinical data. This is a deliberate choice of auditability
over availability: in a regulated clinical context, "the clinician saw the
note but there's no record that they did" is a worse failure mode than
"the clinician's request failed and they retried it a moment later."

## Consequences

A transient audit-table problem (e.g. a lock, a brief disk issue) now
takes down reads, not just audit visibility — availability of the whole
read path is coupled to the audit table's health. We accept that
coupling deliberately; the alternative (fire-and-forget audit writes, or
writing audit in a separate transaction after the fact) creates a window
where the read succeeded and the audit trail silently didn't, which is the
one failure mode this design cannot tolerate. `audit_events` is also
append-only at the grant level (migration 0003 revokes UPDATE/DELETE from
the runtime role), so once a row commits, it commits for good.
