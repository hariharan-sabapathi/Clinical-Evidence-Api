"""decode(encode(x)) == x for representative inputs; malformed cursors are
always a controlled 422 (InvalidCursorError), never an unhandled exception
that would 500. Pure unit tests -- no database.
"""

from __future__ import annotations

from app.core.pagination import Cursor, InvalidCursorError, decode_cursor, encode_cursor

_ROUND_TRIP_CASES = [
    ("2024-01-01T00:00:00", "abc123"),
    ("", ""),
    ("with spaces", "id-with-dashes"),
    ("unicode-éèü", "中文"),
    ('quotes"and\'apostrophes', "id"),
    ("a" * 200, "b" * 200),
]


def test_round_trip() -> None:
    for sort_key, id_ in _ROUND_TRIP_CASES:
        token = encode_cursor(sort_key, id_)
        decoded = decode_cursor(token)
        assert decoded.sort_key == sort_key
        assert decoded.id == id_


def test_cursor_dataclass_round_trip() -> None:
    for sort_key, id_ in _ROUND_TRIP_CASES:
        cursor = Cursor(sort_key=sort_key, id=id_)
        assert Cursor.decode(cursor.encode()) == cursor


def test_arbitrary_bytes_never_crash_decode() -> None:
    """Any junk that isn't a valid cursor must raise InvalidCursorError --
    a controlled 422 at the API layer -- never propagate as some other
    unhandled exception type that would surface as a 500."""
    garbage_samples = [b"", b"\x00\x01\x02", b"not json at all", bytes(range(256)), b"\xff\xfe\xfd"]
    for garbage in garbage_samples:
        token = garbage.decode("latin-1")
        try:
            decode_cursor(token)
        except InvalidCursorError:
            pass  # expected outcome for garbage input
        # Any other exception type is a test failure (uncaught by pytest =
        # the test fails with that exception's traceback).


def test_known_malformed_cursors_are_rejected() -> None:
    for bad in ["not-base64!!!", "", "====", "e30=", "W10=", "eyJhIjogMX0="]:
        try:
            decode_cursor(bad)
            raised = False
        except InvalidCursorError:
            raised = True
        assert raised, f"expected InvalidCursorError for {bad!r}"
