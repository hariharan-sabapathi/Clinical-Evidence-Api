"""decode(encode(x)) == x for every input Hypothesis can generate;
malformed cursors are always a controlled 422 (InvalidCursorError), never
an unhandled exception that would 500. Pure unit tests -- no database.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.core.pagination import Cursor, InvalidCursorError, decode_cursor, encode_cursor

_text = st.text(min_size=0, max_size=200)


@given(sort_key=_text, id_=_text)
def test_round_trip(sort_key: str, id_: str) -> None:
    token = encode_cursor(sort_key, id_)
    decoded = decode_cursor(token)
    assert decoded.sort_key == sort_key
    assert decoded.id == id_


@given(sort_key=_text, id_=_text)
def test_cursor_dataclass_round_trip(sort_key: str, id_: str) -> None:
    cursor = Cursor(sort_key=sort_key, id=id_)
    assert Cursor.decode(cursor.encode()) == cursor


@given(garbage=st.binary(min_size=0, max_size=100))
def test_arbitrary_bytes_never_crash_decode(garbage: bytes) -> None:
    """Any junk that isn't a valid cursor must raise InvalidCursorError --
    a controlled 422 at the API layer -- never propagate as some other
    unhandled exception type that would surface as a 500."""
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
