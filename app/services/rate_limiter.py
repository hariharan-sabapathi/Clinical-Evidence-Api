"""Token-bucket rate limiting in Redis. The check-and-decrement has to be a
single atomic operation or two concurrent requests from the same actor can
both read "1 token left" and both proceed -- a classic TOCTOU race. A Lua
script running inside Redis is atomic with respect to every other command
Redis executes (Redis is single-threaded for command execution), so this
is the standard way to get atomicity without a distributed lock.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
    tokens = capacity
    updated_at = now
end

local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + elapsed * refill_per_second)

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', key, math.ceil(capacity / refill_per_second) + 1)

local retry_after = 0
if allowed == 0 then
    retry_after = math.ceil((requested - tokens) / refill_per_second)
end

return {allowed, retry_after}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


class TokenBucketRateLimiter:
    def __init__(self, redis: Redis, capacity: int, requests_per_minute: int):
        self.redis = redis
        self.capacity = capacity
        self.refill_per_second = requests_per_minute / 60.0
        self._script = redis.register_script(_TOKEN_BUCKET_LUA)

    async def allow(self, actor_key: str, cost: int = 1, *, now: float | None = None) -> RateLimitResult:
        import time

        now = now if now is not None else time.time()
        allowed, retry_after = await self._script(
            keys=[f"ratelimit:{actor_key}"],
            args=[self.capacity, self.refill_per_second, now, cost],
        )
        return RateLimitResult(allowed=bool(int(allowed)), retry_after_seconds=int(retry_after))
