"""Deterministic test of the token-bucket Lua script itself, using the
``now`` override so the assertion doesn't depend on how fast the test
process happens to run (an HTTP round-trip per request is timing-
sensitive under load/coverage instrumentation in a way the Lua script's
own logic, driven by an explicit clock, is not).
"""

from __future__ import annotations

import pytest

from app.services.rate_limiter import TokenBucketRateLimiter

pytestmark = pytest.mark.integration


async def test_51st_request_at_the_same_instant_is_rejected(app_instance):
    limiter = TokenBucketRateLimiter(app_instance.state.redis, capacity=50, requests_per_minute=50)
    now = 1_700_000_000.0

    results = [await limiter.allow("actor-x", now=now) for _ in range(51)]

    assert all(r.allowed for r in results[:50])
    assert not results[50].allowed
    assert results[50].retry_after_seconds >= 1


async def test_tokens_refill_over_time(app_instance):
    limiter = TokenBucketRateLimiter(app_instance.state.redis, capacity=10, requests_per_minute=60)
    now = 1_700_000_000.0

    for _ in range(10):
        assert (await limiter.allow("actor-y", now=now)).allowed
    assert not (await limiter.allow("actor-y", now=now)).allowed

    # 60 requests/min == 1 token/sec -- 5 seconds later, 5 tokens are back.
    later = now + 5.0
    results = [await limiter.allow("actor-y", now=later) for _ in range(10)]
    assert sum(r.allowed for r in results) == 5
