"""Cursor-based pagination. The cursor is a base64url-encoded JSON 2-tuple
``(sort_key, id)`` -- never a raw offset.

Why not offset (``LIMIT/OFFSET``): offset pagination defines "page 2" as
"skip N rows of whatever the query returns right now". If a row is
inserted (or deleted) ahead of the current page between two requests for
the same listing, every row after it shifts by one position and the
client either re-sees a row it already had or silently skips one it never
saw -- there is no way to detect this from the client side. It gets worse
under concurrent writes, which is the normal operating condition for this
service (ingestion workers are writing documents while clinicians are
paging through them). A cursor built from a stable, unique sort key (here:
``(created_at, id)``, ties broken by id) doesn't have this failure mode:
"give me everything after this specific row" stays correct regardless of
what got inserted or deleted elsewhere in the table, because the next
page's WHERE clause is anchored to a row, not a position.

This is also the answer to "why not just use the row's id as the cursor":
ids alone don't express insertion order visible to the API's sort
(``ORDER BY created_at``), and ``created_at`` alone isn't unique enough to
resume from cleanly when many rows share a timestamp -- hence the tuple.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any


class InvalidCursorError(ValueError):
    pass


@dataclass(frozen=True)
class Cursor:
    sort_key: str
    id: str

    def encode(self) -> str:
        raw = json.dumps([self.sort_key, self.id], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def decode(token: str) -> Cursor:
        try:
            raw = base64.urlsafe_b64decode(_pad(token))
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001 - any decode failure is the same 422 to the caller
            raise InvalidCursorError(f"malformed cursor: {token!r}") from exc

        if (
            not isinstance(data, list)
            or len(data) != 2
            or not isinstance(data[0], str)
            or not isinstance(data[1], str)
        ):
            raise InvalidCursorError(f"malformed cursor payload: {data!r}")
        return Cursor(sort_key=data[0], id=data[1])


def _pad(token: str) -> str:
    return token + "=" * (-len(token) % 4)


def encode_cursor(sort_key: Any, id_: Any) -> str:
    return Cursor(sort_key=str(sort_key), id=str(id_)).encode()


def decode_cursor(token: str) -> Cursor:
    return Cursor.decode(token)
