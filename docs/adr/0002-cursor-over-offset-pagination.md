# 0002: Cursor pagination over offset pagination

## Context

Every list endpoint (`/v1/patients`, `/v1/patients/{id}/documents`,
`/v1/audit`) is read concurrently with writes: clinicians page through
results while ingestion workers are inserting new documents, and audit
events are appended continuously. `LIMIT/OFFSET` defines "page 2" as "skip
N rows of whatever the query returns right now" — if a row is inserted or
deleted ahead of the current page between two requests, every row after
it shifts position, and the client either re-sees a row it already had or
silently skips one it never saw. There is no way to detect this from the
client side, and it gets worse, not better, under load.

## Decision

Cursors are a base64url-encoded JSON 2-tuple `(sort_key, id)` (see
`app/core/pagination.py`), anchored to `ORDER BY sort_key ASC, id ASC` and
resumed with `WHERE (sort_key, id) > (:cursor_sort_key, :cursor_id)`. Ties
on `sort_key` (two rows created in the same millisecond) are broken by
`id`, which is unique, so the tuple is always a stable resume point
regardless of what else is inserted or deleted elsewhere in the table.
Malformed or tampered cursors decode to a controlled `InvalidCursorError`
→ `422`, never a crash — proven for arbitrary input by the Hypothesis
round-trip property test in `tests/property/test_cursor_codec.py`.

## Consequences

Pagination is correct under concurrent writes, which is the normal
operating condition here, at the cost of cursors being opaque (no "jump
to page 7") and one extra encode/decode step per request. Query plans need
a composite index on `(sort_key, id)` to stay cheap, which we already have
on every paginated table's natural sort columns.
