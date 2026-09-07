"""Prometheus metrics. Bucket boundaries for the two latency histograms are
picked deliberately for a 25ms-2s service, not left at the client's default
(.005 .. 10s) buckets, which would put almost every real observation in the
service's normal range into one or two buckets and make the p95/p99
`histogram_quantile` estimate nearly meaningless. 25ms is roughly the floor
for a cache-hit query; 2s is the point past which we'd rather see an alert
than a finer-grained bucket.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest

registry = CollectorRegistry()

_LATENCY_BUCKETS = (0.025, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0, 1.5, 2.0, 5.0)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["route", "method", "status"],
    buckets=_LATENCY_BUCKETS,
    registry=registry,
)

retrieval_duration_seconds = Histogram(
    "retrieval_duration_seconds",
    "Time spent in the retrieval stage (embedding + vector search)",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
    registry=registry,
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "Tokens sent to / received from the LLM",
    ["direction", "model"],
    registry=registry,
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "Estimated USD cost of LLM calls",
    ["model"],
    registry=registry,
)

ingest_jobs_total = Counter(
    "ingest_jobs_total", "Ingest jobs reaching a terminal state", ["status"], registry=registry
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total", "Requests rejected by the token-bucket limiter", ["actor_role"], registry=registry
)

circuit_breaker_state = Counter(
    "circuit_breaker_state_transitions_total", "Circuit breaker state transitions", ["to_state"], registry=registry
)


def render_latest() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
