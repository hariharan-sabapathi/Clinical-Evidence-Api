# Performance: naive → profiled → fixed

This follows the same loop as the NASSCOM Telegram-bot optimization
story in the resume this repo supports — ship naive, load-test it,
profile it to find out where time *actually* goes rather than where you
assume, fix that, re-run the identical test, publish both numbers. The
twist here is that profiling didn't confirm the assumption; it corrected
it, and the fix that mattered turned out to be a different one than the
one originally planned. That's reported honestly below rather than
smoothed over, because it's the more useful result of the two. A second
round of the same discipline — checking a headline number against Little's
Law, then checking a tail-latency guess against the actual per-request
timeline instead of asserting it — turned up a real, unresolved finding
that's reported the same way: as far as the evidence goes, no further.

## Methodology

- **Corpus:** `scripts/seed_benchmark_data.py` — 60 patients, 300 chunks
  each (18,000 `documents` rows), 60 clinicians each assigned to exactly
  one patient. Large enough that a sequential vector scan is real work,
  not noise.
- **Load:** `k6_query_load.js` — 50 virtual users, 3 minutes, a 5-question
  realistic mix, `sleep(0.2)` think time between iterations, auth once per
  VU in `setup()` so login cost doesn't pollute the per-request numbers.
  Every run below used the identical script and parameters; only the
  server-side configuration changed. This is a **closed-loop** load model
  (fixed VU count, each VU blocks on its own previous request) — that
  detail turns out to matter for reading the throughput numbers correctly,
  see "Why 2.3×, not 4×" below.
- **Rate limit raised for the duration of these runs**
  (`RATE_LIMIT_REQUESTS_PER_MINUTE` set high via an env var) — the
  production default (50 req/min per actor) is sized for interactive
  clinician use, not a 50-VU synthetic burst against 60 shared actors, and
  would otherwise measure the rate limiter instead of the query path. This
  is a load-test configuration change, not a code change.
- Raw k6 terminal output and `--summary-export` JSON for every run below
  are committed in this directory (`*_raw_output.txt`, `*_summary.json`).
  Per-request timing for the two diagnostic re-runs referenced below is
  summarized in `latency_timeline_analysis.txt` (5-second buckets of
  `http_req_duration`, derived from `k6 run --out json=...`; the raw
  per-request JSON is ~140MB and isn't committed).

## Step 1 — ship the naive version, load test it

`v0.1-naive`: `SEMANTIC_CACHE_ENABLED=false`, no vector index (migration
`0007` not yet applied — plain sequential scan over each patient's
chunks), single uvicorn worker process.

```
avg=270.79ms  p90=461.10ms  p95=484.34ms  max=920.24ms
throughput=97.6 req/s   19,155 requests   0.00% failed
```

(`naive_raw_output.txt`, `naive_summary.json`)

## Step 2 — profile it

Assumption going in: this is slow because every query re-embeds the
question and does a full sequential scan over a patient's chunks. `py-spy`
(`flamegraph.svg`, 8,701 samples at 200 Hz, captured against the live
server under sustained k6 load — reproduce with
`py-spy record -o flamegraph.svg --pid <uvicorn-pid> --duration 25 --rate 200`)
says otherwise:

| Where the samples actually are | Share |
|---|---|
| ASGI/Starlette/FastAPI request plumbing (routing match, middleware stack, `run_asgi`) | ~15% |
| FastAPI dependency-injection resolution (`solve_dependencies`, nested `Depends`) | ~8% |
| Opening/closing the request-scoped DB transaction (`session_as`, `SET LOCAL`, commit) | ~8% |
| SQLAlchemy ORM `execute()` call machinery (separate from the query's own runtime) | ~14% |
| The actual retrieval query (`retrieve()`, the `ORDER BY embedding <=> ...`) | a few % |
| Semantic cache lookup (Redis `LRANGE` + Python-side cosine-similarity loop) | ~2% |

At 300 chunks per patient, the vector search itself is already
low-single-digit milliseconds — nowhere near the dominant cost. The
dominant cost is **per-request framework and connection-management
overhead, multiplied by 50 concurrent virtual users all being served by
one Python process.** One `asyncio` event loop, however non-blocking its
I/O, still executes Python bytecode for 50 concurrent requests one slice
at a time.

## Step 3 — fix it (the originally planned fix)

Applied both changes from the plan: `SEMANTIC_CACHE_ENABLED=true`
(default) and the HNSW index (migration `0007`,
`CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)`). Same
script, same parameters, still one worker process:

```
avg=290.30ms  p90=437.67ms  p95=457.47ms  max=890.16ms
throughput=93.7 req/s   18,421 requests   0.00% failed
```

(`optimized_raw_output.txt`, `optimized_summary.json`)

**Essentially no change** (p95 improves modestly, ~5.5%; average is
actually a hair worse, within run-to-run noise). This is the expected
consequence of step 2's finding, not a bug: caching and indexing a query
stage that was never the bottleneck at this corpus size doesn't move the
number that's dominated by something else entirely. Reported here because
the honest result of testing a hypothesis is the point of profiling
before fixing, not after.

## Step 4 — fix the thing profiling actually found

One more variable, isolated: same cache + index config as step 3, `uvicorn
--workers 4` (this machine has 4 vCPUs) instead of 1. Nothing else
changed — same script, same parameters, same rate-limit/budget overrides.

```
avg=8.57ms   p90=10.12ms   p95=12.47ms   max=639.18ms
throughput=220.1 req/s   43,166 requests   0.00% failed
```

(`multiworker_raw_output.txt`, `multiworker_summary.json`)

## Reading this correctly: a saturation analysis, not a speed claim

The naive-vs-4-worker gap above (270.79ms → 8.57ms) looks like "31.6×
faster," and that framing is wrong. Applying Little's Law (L = λW, mean
requests in flight = throughput × mean latency) to each run:

| Run | Throughput (λ) | Avg latency (W) | Requests in flight (L = λW) |
|---|---:|---:|---:|
| naive (1 worker) | 97.6 req/s | 0.2708 s | **≈ 26** |
| +cache +HNSW (1 worker) | 93.7 req/s | 0.2903 s | **≈ 27** |
| +cache +HNSW +4 workers | 220.1 req/s | 0.00857 s | **≈ 1.9** |

A single process held roughly 26–27 requests in flight simultaneously
under 50 offered VUs — that's a system **saturated at the offered load**,
where most of the 270–290ms is queueing for the one process's attention,
not work being done. The four-worker run holds under 2 requests in
flight on average — it is **not saturated**; its latency is close to
pure service time. So the honest headline isn't "4 workers made requests
31.6× faster." It's: **one process was saturated at ~94–98 req/s under
this load, and latency was dominated by queue depth; four workers raised
capacity to 220 req/s, moving the system off the knee and collapsing the
queueing component.** The number that actually describes the change in
capacity is **2.3×** (throughput), and that's the one to trust; the 31.6×
describes a queueing artifact of comparing a saturated system to an
unsaturated one, not a 31.6× improvement in how fast the server does the
same piece of work.

## Why 2.3×, not 4×

Four processes on four vCPUs raising throughput 2.3× instead of ~4× is
worth explaining rather than shrugging off as "some overhead somewhere."
Two candidate explanations, checked against actual data rather than
assumed:

**Checked and ruled out: Postgres connection pool / Redis contention.**
Sampled `pg_stat_activity` and Redis `INFO` every 10s during the 4-worker
run (`pg_activity_samples_during_multiworker_run.txt`,
`redis_stats_samples_during_multiworker_run.txt`):

- Postgres: **1 active query** at every sample (briefly 3, once 6), against
  a pool of up to 40 connections (4 workers × `db_pool_size=10`). Not
  remotely saturated.
- Redis: **800–1,200 ops/sec** throughout. A single Redis instance
  handling simple commands typically sustains tens of thousands of
  ops/sec — this is roughly 1–2% of that, not a bottleneck.

**Best-supported explanation: the benchmark's own closed-loop ceiling.**
`k6_query_load.js` runs a fixed 50 VUs, each sleeping 0.2s between
iterations — a closed queueing model, not an open one. As service time W
approaches zero, the maximum throughput this script can ever generate
approaches N/Z = 50 / 0.2s = **250 req/s**, independent of how fast the
server is. The measured 220.1 req/s is **88% of that ceiling** — this
benchmark's own concurrency, not the server or its dependencies, is the
likely limiting factor once the server got fast. This means 220 req/s —
and the **2.3× throughput figure built from it — is a floor, not a
measured ceiling**: the 4-worker configuration was never pushed to
saturation by this benchmark, so its actual capacity is unknown and
higher than 220 req/s. That's a stronger, better-supported claim than
"2.3× faster," and it's the one to trust over the specific multiplier. A
benchmark designed to find the real ceiling would need more VUs, shorter
think time, or an open-model load generator. I did not re-run with a
redesigned script to find it — that's a concrete next step this writeup
is flagging, not one it's claiming to have done.

## Tail latency: investigated, not fully resolved

The 4-worker run's `max=639.18ms` against `avg=8.57ms` is a real tail
worth acknowledging — and worth checking against the actual per-request
timeline rather than guessing at a cause, which is the same discipline
step 2 already required. Two re-runs with `k6 run --out json=...` for
full per-request timestamps (bucketed in
`latency_timeline_analysis.txt`) turned up something more specific, and
less tidy, than either guess I started with:

- **First guess, checked and rejected: cold connections at worker start.**
  If that were the cause, the slow requests would cluster in the first
  second or two. They don't. Run A shows an elevated period from
  **t≈15s to t≈20s** (plausibly startup-related — new connections, first
  JIT/cache warmup) that clears by t=20s, but then a **second, sustained**
  elevated period from **t≈45s onward that lasts for the rest of the
  196s run** (avg ~85–95ms, p95 ~360–380ms in every 5-second bucket from
  45s to the end) — a recurring condition, not a one-time startup cost.
- **Second guess, tested with a real experiment, not confirmed as the sole
  cause: Redis RDB background-save.** The rate limiter + token budget +
  semantic cache generate on the order of 1,000 Redis ops/sec, easily
  tripping Redis's default `save 60 10000` snapshot trigger; a `BGSAVE`
  fork's copy-on-write pause is a well-known source of exactly this kind
  of episodic latency. I disabled it (`redis-cli CONFIG SET save ""`),
  cleared cache/rate-limit state, and re-ran the identical script (Run B).
  **The sustained elevated period did not disappear — it recurred, delayed,
  from t≈150s to the end of that run instead.** That rules out RDB
  background-save as the *sole* explanation, since it was disabled for
  Run B; it doesn't rule it out as *a* contributing factor, since I only
  have two data points and the recurrence timing (t≈45s in Run A, t≈150s
  in Run B) isn't consistent enough to pin on a single fixed-interval
  cause with confidence.
- **Not yet checked:** Postgres autovacuum/checkpoint activity (checkpoints
  are timed at a 5-minute default and I don't have a before/after delta
  isolated to either run), and synchronized Python garbage-collection
  pauses across the 4 worker processes (plausible given they started
  together under similar allocation pressure, not instrumented here).
- **A finding in its own right, separate from the tail itself:** the
  *originally published* 4-worker run (`multiworker_summary.json`,
  `avg=8.57ms`, `p95=12.47ms`) shows no sign of a tail this large in its
  own percentiles, while both diagnostic re-runs of the identical
  configuration did. That's run-to-run variability in a metric I'm
  otherwise reporting as a clean number, and it means the 8.57ms/220 req/s
  figures above should be read as "achieved once, under this exact
  config" rather than "reliably reproducible" until this is root-caused.
  I'm reporting that gap rather than quietly re-running until I got a
  clean number, because the latter is precisely the kind of
  vibes-based benchmarking this whole exercise is arguing against.

What I'd do next with more time: re-run with Postgres checkpoint/autovacuum
logging turned up and Python's `gc` instrumented (`gc.callbacks` timing)
across all 4 workers, correlated against the same 5-second buckets, to
actually attribute the recurring episode instead of narrowing it by
elimination.

## Before / after

| Metric | v0.1-naive (1 worker, no cache/index) | +cache +HNSW (1 worker) | +cache +HNSW +4 workers † |
|---|---:|---:|---:|
| avg latency | 270.79 ms | 290.30 ms | 8.57 ms |
| p95 latency | 484.34 ms | 457.47 ms | 12.47 ms |
| throughput | 97.6 req/s | 93.7 req/s | 220.1 req/s |
| requests in flight (L = λW) | ≈26 | ≈27 | ≈1.9 |
| error rate | 0.00% | 0.00% | 0.00% |

† **This column is one run, not a settled number — don't read it as
reproducible.** These exact figures (8.57ms / 12.47ms / 220.1 req/s) come
from the run reported in Step 4. Two later re-runs of the identical
configuration, done specifically to investigate that run's `max=639ms`
tail (see below), both showed a sustained elevated-latency period this
run did not: avg climbing to roughly 85–95ms with p95 around 350–380ms
for well over half the run, not the clean 8.57/12.47ms shown here. The
honest range for this configuration, pending the unresolved
root-causing below, is closer to **avg 8–95ms / p95 12–380ms** depending
on whether that episode is present, not the single clean value the table
column implies in isolation. Throughput carries the same caveat, and the
**2.3× figure is additionally a floor, not a ceiling** (see "Why 2.3×,
not 4×" above) — both facts point the same direction: treat this
configuration as "meaningfully faster and higher-capacity than one
worker, with a real capacity ceiling that is still unknown," not as "a
verified 8.57ms / 220 req/s system."

## What this actually demonstrates

Not "we added a cache and it got faster" — the data says that specific
story is false at this corpus scale, and reporting the true negative is
more valuable than a comfortable fabrication. Not "4 workers made it
31.6× faster" either — that compares a saturated system's queueing-
dominated latency to an unsaturated one's service-time-dominated latency,
which is a queueing artifact, not a throughput claim; the number that
actually describes the change is the 2.3× capacity gain. And not "here's
a clean explanation for the tail latency" — the honest state of that
investigation is two hypotheses tested, both incomplete, reported as
such. What all three demonstrate together is the discipline the exercise
is actually testing: measure before assuming, let the flame graph and
Little's Law — not intuition — decide what the data means, and when a
follow-up check doesn't resolve cleanly, say so instead of picking the
cleaner-looking number. The semantic cache and HNSW index are still
correct, tested (see
`tests/security/test_semantic_cache_patient_isolation.py` and
`tests/concurrency/test_cache_stampede.py`), and worth having — they're
the change that matters as corpus-per-patient size and query repetition
grow past what this benchmark's 60-patient corpus exercises; they were
just not the change that mattered *for this benchmark, at this scale*.
Process-level concurrency was, up to the benchmark's own closed-loop
ceiling — see "What breaks at 100x" in the main README for what happens
past that.

## Reproduce

```bash
# 1. Seed a large corpus
python scripts/seed_benchmark_data.py

# 2. Naive baseline
alembic downgrade -1   # drops the HNSW index (migration 0007)
SEMANTIC_CACHE_ENABLED=false uvicorn app.main:app --port 8000
BASE_URL=http://localhost:8000 k6 run --summary-export=naive_summary.json benchmarks/k6_query_load.js

# 3. Optimized (cache + index), same process count
alembic upgrade head
uvicorn app.main:app --port 8000
BASE_URL=http://localhost:8000 k6 run --summary-export=optimized_summary.json benchmarks/k6_query_load.js

# 4. Optimized + multi-process
uvicorn app.main:app --port 8000 --workers 4
BASE_URL=http://localhost:8000 k6 run --summary-export=multiworker_summary.json benchmarks/k6_query_load.js

# 5. Diagnose the tail: full per-request timestamps instead of just percentiles
k6 run --out json=timeseries.json benchmarks/k6_query_load.js
# then bucket http_req_duration by time to see *when* slow requests occur,
# rather than reading p95/max in isolation
```
