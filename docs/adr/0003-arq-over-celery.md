# 0003: arq over Celery for async ingestion

## Context

`POST /v1/ingest/bundles` needs a job queue: validate synchronously,
enqueue the real parse/chunk/embed/write work, return 202 immediately.
Celery is the default answer to "I need a task queue in Python," but it
brings a broker abstraction (this service only ever needs Redis, never
RabbitMQ/SQS), a separate result-backend concept, and a configuration
surface (routing, exchanges, `celery beat`, worker pools) sized for
problems this service doesn't have — one job type, one queue, done.

## Decision

Use `arq`: Redis-backed, `asyncio`-native (no thread/process pool bridging
into the same async DB engine and HTTP client the rest of the service
already uses), and small enough that `app/workers/settings.py` is 25
lines. Retries with backoff are explicit application code
(`app/workers/ingest.py`) rather than a framework feature, which is more
code but exactly matches the spec's jittered-backoff requirement (1s, 4s,
16s ±25%) instead of fighting a framework default.

## Consequences

We give up Celery's ecosystem (routing between multiple queues/brokers,
`celery beat` periodic tasks, Flower monitoring) — none of which this
service needs today. If a second job type with different concurrency or
routing needs ever appears, arq's simplicity may need revisiting; for one
job type behind one Redis instance, it's the smaller, easier-to-reason-
about system.
