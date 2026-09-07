"""A textbook three-state circuit breaker guarding the LLM call.

closed --(N consecutive failures)--> open --(cooldown elapses)--> half-open
  ^                                                                  |
  +---------------------(probe succeeds)----------------------------+
                          (probe fails: back to open, cooldown resets)

When the breaker is open, ``/query`` does not attempt the model call at
all -- it degrades to retrieval-only: ranked evidence, real citations,
``"answer": null``, ``"degraded": true``, HTTP 200. That 200 (not 503) is
deliberate: the clinician still gets the part that matters (the evidence),
and a 503 would make a perfectly usable partial response look like a
failure to any client that branches on status code. See
docs/adr/0004-degrade-to-retrieval-only.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    open_seconds: float = 30.0
    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> BreakerState:
        if self._state == BreakerState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.open_seconds:
                self._state = BreakerState.HALF_OPEN
        return self._state

    def allow_request(self) -> bool:
        return self.state in (BreakerState.CLOSED, BreakerState.HALF_OPEN)

    def on_success(self) -> None:
        self._consecutive_failures = 0
        self._state = BreakerState.CLOSED
        self._opened_at = None

    def on_failure(self) -> None:
        if self.state == BreakerState.HALF_OPEN:
            # The probe failed -- reopen and restart the cooldown.
            self._state = BreakerState.OPEN
            self._opened_at = time.monotonic()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = time.monotonic()

    def force_open(self) -> None:
        """Test/ops hook."""
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
