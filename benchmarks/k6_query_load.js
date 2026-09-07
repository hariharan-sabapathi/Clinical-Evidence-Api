// k6 load test for POST /v1/patients/{id}/query.
//
// 50 virtual users, 3 minutes, a realistic query mix (a handful of
// recurring clinical questions against a rotating set of real
// patient/clinician pairs seeded by scripts/seed_benchmark_data.py).
// Auth happens once per VU in setup() -- as it would for a real client
// holding a JWT for its access-token lifetime -- so per-iteration cost
// measures the query endpoint itself, not login overhead.
//
// Usage:
//   BASE_URL=http://localhost:8000 k6 run benchmarks/k6_query_load.js
//
// Run identically before and after the optimization (see
// benchmarks/README.md) -- same script, same parameters, only the
// server-side configuration (SEMANTIC_CACHE_ENABLED, the HNSW index)
// differs between the two runs.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const actors = JSON.parse(open('./actors.json'));

const QUESTIONS = [
  'what medications is the patient currently on?',
  'is the patient up to date on immunizations?',
  'what is the most recent blood pressure reading?',
  'does the patient have any known drug allergies?',
  'what is the plan for follow up?',
];

export const queryDuration = new Trend('query_duration_ms', true);

export const options = {
  scenarios: {
    query_mix: {
      executor: 'constant-vus',
      vus: 50,
      duration: '3m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  const authed = actors.map((actor) => {
    const res = http.post(
      `${BASE_URL}/v1/auth/token`,
      `username=${encodeURIComponent(actor.email)}&password=${encodeURIComponent(actor.password)}`,
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
    );
    const token = res.status === 200 ? res.json('access_token') : null;
    return { token, patient_id: actor.patient_id };
  });
  return { authed: authed.filter((a) => a.token !== null) };
}

export default function (data) {
  const actor = data.authed[Math.floor(Math.random() * data.authed.length)];
  const question = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];

  const res = http.post(
    `${BASE_URL}/v1/patients/${actor.patient_id}/query`,
    JSON.stringify({ q: question }),
    {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${actor.token}`,
      },
    }
  );

  queryDuration.add(res.timings.duration);
  check(res, {
    'status is 200': (r) => r.status === 200,
  });

  sleep(0.2);
}
