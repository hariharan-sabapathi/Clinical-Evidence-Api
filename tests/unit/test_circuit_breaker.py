from __future__ import annotations

import time

from app.services.circuit_breaker import BreakerState, CircuitBreaker


def test_closed_by_default():
    breaker = CircuitBreaker(failure_threshold=3, open_seconds=30)
    assert breaker.state == BreakerState.CLOSED
    assert breaker.allow_request()


def test_opens_after_threshold_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, open_seconds=30)
    breaker.on_failure()
    breaker.on_failure()
    assert breaker.state == BreakerState.CLOSED
    breaker.on_failure()
    assert breaker.state == BreakerState.OPEN
    assert not breaker.allow_request()


def test_success_resets_the_failure_count():
    breaker = CircuitBreaker(failure_threshold=3, open_seconds=30)
    breaker.on_failure()
    breaker.on_failure()
    breaker.on_success()
    breaker.on_failure()
    breaker.on_failure()
    assert breaker.state == BreakerState.CLOSED  # would be OPEN without the reset


def test_transitions_to_half_open_after_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=0.05)
    breaker.on_failure()
    assert breaker.state == BreakerState.OPEN
    time.sleep(0.1)
    assert breaker.state == BreakerState.HALF_OPEN
    assert breaker.allow_request()


def test_half_open_probe_failure_reopens_and_resets_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=0.05)
    breaker.on_failure()
    time.sleep(0.1)
    assert breaker.state == BreakerState.HALF_OPEN
    breaker.on_failure()
    assert breaker.state == BreakerState.OPEN
    assert not breaker.allow_request()


def test_half_open_probe_success_closes_the_breaker():
    breaker = CircuitBreaker(failure_threshold=1, open_seconds=0.05)
    breaker.on_failure()
    time.sleep(0.1)
    assert breaker.state == BreakerState.HALF_OPEN
    breaker.on_success()
    assert breaker.state == BreakerState.CLOSED
