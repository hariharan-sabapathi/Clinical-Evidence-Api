# Evaluation methodology

## Ground truth is independent of the corpus

```
Clinical notes (7,761)  ->  the retrieval corpus  (corpus/notes.py)
Structured FHIR         ->  the ground truth       (evalset/ground_truth.py)
```

Every answer in `data/eval_set.jsonl` is computed directly from structured
FHIR resources (`Condition`, `MedicationRequest`, `Observation`,
`AllergyIntolerance`, etc.) — `evalset/templates.py` never reads note text to
produce a question or an answer. The notes are only ever the thing being
searched, never the source of truth. This means a retrieval failure can't be
an artifact of the eval set having been written from the same text it's
tested against.

## §1.2 — lenient vs. strict scoring

Two scoring policies, computed in every table:

- **Lenient** — a hit if any retrieved chunk is in `permissive_chunk_ids`
  (every chunk, for that patient, containing the answer's literal entity
  string — see `evalset/permissive.py`). Measures raw information retrieval.
- **Strict** — a hit if a retrieved chunk is in `gold_chunk_ids` (the
  encounter(s) the fact actually originates from). Measures temporal
  precision.

`permissive_chunk_ids` is recomputed per chunk level at eval time (not fixed
to one chunking scheme) — a chunk's boundaries change with the strategy under
test, so which chunks "contain the answer" has to change with it too. The
persisted `gold_chunk_ids`/`permissive_chunk_ids` in `data/eval_set.jsonl`
are pinned to the `full_note` reference level; `eval/runner.py` recomputes
both from each question's `gold_encounter_ids` and `answer_terms` for every
other level.

## Statistical rigor (§6.4) — bootstrap CIs and real paired significance tests

Every executed row in `results/ablations.csv` carries a 95% bootstrap CI
(1,000 resamples, `eval/metrics.py::bootstrap_ci`) rather than a bare point
estimate. Beyond that, `scripts/run_extra_analyses.py` runs
`eval/metrics.py::paired_bootstrap_test` — implemented from the start, but
an earlier version of this pipeline never actually called it against real
results — on four real, question-aligned comparisons:

| Comparison | Δ Recall@5 (strict) | 95% CI | Significant? |
|---|---|---|---|
| `fixed_512` baseline vs. `full_note` | +0.310 | [+0.138, +0.483] | Yes |
| top_k=10 vs. top_k=5 | +0.241 | [+0.103, +0.414] | Yes |
| within-patient (oracle) vs. cross-patient | +0.069 | [0.000, +0.172] | No |
| bm25+patient_filter vs. bm25 | +0.069 | [0.000, +0.172] | No |

`results/significance_tests.csv` has the full output. The point worth
stating plainly, per the spec's own emphasis: the patient filter and the
within-patient oracle restriction both look like real improvements at the
level of a point estimate, and neither survives a paired significance test
at this sample size (n=29). Reporting only the point estimates would have
implied a stronger claim than the data supports.

## Groundedness filter (§3.2)

After generating each candidate, `evalset/grounding.py` checks whether the
answer's literal entity terms (not the generated English question/answer
sentence — the raw display strings, e.g. a medication name) appear in the
text of the gold encounter's note(s). A candidate is kept either way and
labeled `grounded_in_note: true/false` — nothing is discarded for failing
this check, per the spec's instruction to keep a labeled subset of the
"structured record has it, no note does" case.

**Measured grounding rate, by question type** (`data/eval_set.jsonl`,
n=194 total):

| Question type | Grounded |
|---|---|
| Condition-, medication-, immunization-, allergy-derived (9 types) | **100%** |
| `vital_lookup` (blood pressure) | **0%** |
| `lab_trend` (lab value trends) | **0%** |
| `hand_written_ambiguous` (by design — no answer terms) | 0% |

This split is essentially binary, not a gradient: every question whose
answer is a *named clinical entity* (a condition, a drug, a vaccine, an
allergen) grounds at 100%, because Synthea's note generator restates entity
display strings verbatim in the relevant section. Every question whose
answer is a *numeric measurement* (a blood pressure reading, a lab trend)
grounds at 0%, because the six note sections never state raw vital-sign or
lab values — only entity names and, for labs, which panels were ordered.
`docs/corpus-properties.md` uses this same distinction to explain the
`structured` chunking variant's motivation. Overall grounding: 84.5%
(164/194).

One further, non-obvious grounding gap surfaced during this check: even
though `AllergyIntolerance` is a *patient-level* fact (§1, no encounter
link), checking a single reference encounter's note against it initially
showed 0% groundedness for allergy-positive questions. Widening the check to
"any of this patient's notes" (matching how permissive-set scoping already
works) raised it to 100% for most patients, but a broader manual check found
that only 2 of 16 patients with a real (non-generic) allergy code have that
allergen name appear in *any* of their notes' Allergies section, ever — most
of the remaining 14 patients' notes still read "No Known Allergies" despite
a documented `AllergyIntolerance` resource existing for them. That is a
genuine desynchronization between Synthea's structured-resource generation
and its note-text generation, not a bug in this repo's grounding check —
and it is a stronger, cleaner version of the exact phenomenon §3.2 asks this
project to surface.

## Paraphrase augmentation (§3.3) — a documented substitute, now actually preferred in evaluation

The spec calls for LLM-generated paraphrases, verified by hand. This project
was built in a sandbox with no reachable LLM API (see the top-level README's
"Environment constraints"), so `evalset/paraphrase.py` implements a
rule-based paraphraser instead: phrasing-pattern synonym substitution (e.g.
"What medications was" → "Which drugs was", "How many times" → "On how many
occasions"), applied to 40 grounded, answerable programmatic questions.

§3.3 also says to "evaluate on paraphrases rather than raw templates" — an
earlier version of this pipeline generated paraphrases but evaluated on
whatever was in the test split regardless, which meant a question and its
paraphrase could both be scored, double-counting the same underlying fact.
`evalset/paraphrase.py::prefer_paraphrases` fixes this: for every base
question that has a paraphrase, the raw template is dropped from the
evaluated set and only the paraphrase counts. `eval/runner.py` (via
`scripts/run_ablations.py`) and `scripts/label_failures.py` both run this
filter before computing anything. On the current eval set, 11 of the test
split's questions have a paraphrase, so the evaluated test split is 29
questions, not the 40 in `data/eval_set.jsonl` with `split: test` — see
`results/ablations.csv`'s `n_questions` column for the number actually used
in each row.

An earlier version also attempted a structural rewrite (fronting the date
clause: "What X was {name} Y during {date}?" → "During {name}'s {date}, what
X did they Y?"). It was found, during a manual read-through of the generated
output, to break grammatically on this corpus's multi-word patient names
("Don899 Eliseo499 Swift555") — a lazy regex match split the name from the
verb at the wrong word boundary. That rewrite was removed rather than
patched further; the synonym-substitution-only version produces less
dramatic variation but never breaks grammar, which matters more for output a
human is meant to spot-check. This is called out explicitly rather than
silently shipping the safer version — the diversity gap versus real LLM
paraphrasing is real, and upgrading to one is a documented follow-up that
just needs an API key wired into `generation/llm_client.py`.

## Human validation (§3.4)

46 (question, answer) pairs were read against their source FHIR data and
generated note text during the build, across every question type in the eval
set. Two issues were found and fixed before the eval set was finalized:

1. `gen_allergy_positive` initially included `AllergyIntolerance` records
   coded as the generic finding "Allergic disposition (finding)" as if it
   were a specific substance, producing a nonsensical question ("...allergy
   to Allergic disposition?"). Fixed by excluding that code
   (`NON_SUBSTANCE_ALLERGY_CODES` in `evalset/templates.py`).
2. The paraphrase structural rewrite described above, which broke grammar on
   multi-word names. Removed (see above).

Zero further issues were found in a post-fix sample of 34 additional pairs
stratified across all 17 question types. This was performed by the person
building the pipeline against the real dataset, not a separate blinded human
reviewer — reported as such rather than implying an independent check that
didn't happen.

## Stratified patient split (§3.5)

Encounter counts range 10–672 per patient; a naive 60/20/20 split could put
most of the corpus in one fold. `evalset/split.py` sorts patients by
encounter count and assigns folds round-robin over a repeating
train/train/.../dev/.../test cycle sized to the target fractions, so every
fold gets a spread of low-, medium-, and high-encounter-count patients.

Measured result: **72 / 24 / 24 patients** (train/dev/test) — matching the
spec's target split sizes exactly — corresponding to **111 / 43 / 40
questions** in `data/eval_set.jsonl` before paraphrase preference is applied
(38 / 43 / 29 answerable-and-evaluated after it, in the test split's case).

**`data/splits.yml` is the actual source of truth**, not just a persisted
by-product of the split computation. An earlier version wrote the file but
never read it back — every script trusted the `split` field baked into each
question at generation time instead, so hand-editing `splits.yml` (to fix a
mistake, or re-balance a fold) would have silently done nothing.
`evalset/split.py::load_eval_questions` is now the one function every script
uses to load questions: it reads `eval_set.jsonl`, then immediately
overwrites every question's `.split` from `splits.yml`'s patient→fold
mapping, and raises if a patient is missing from the file (rather than
silently keeping a stale value) — see `tests/test_split_authority.py` for
the regression test proving this.

## Per-tier and programmatic-vs-hand-written breakdown, within/cross-patient (§6.5)

`scripts/run_extra_analyses.py` computes Recall@5 (lenient/strict), MRR@10,
and nDCG@10 broken down by tier and by question origin (programmatic —
including paraphrases of programmatic templates — vs. hand-written), plus
the within-patient/cross-patient comparison discussed in
`docs/corpus-properties.md`. Real numbers, real bootstrap CIs, on the
reference config's test split: `results/breakdown_by_tier.csv`,
`results/breakdown_by_generation.csv`, `results/within_vs_cross_patient.csv`.
Tier and generation-origin group sizes are small (tier 0: n=3; hand-written:
n=3) at this eval-set size — reported with their CIs rather than
overstated.

## What's in `data/eval_set.jsonl`

194 questions: 49 tier-1 (single-hop factual), 58 tier-2 (multi-hop/
temporal), 52 tier-3 (aggregation/negation), 35 hand-written (tier 0);
119 programmatic + 40 rule-based paraphrases + 35 hand-written. 12 questions
(6.2%) are allergy-negation true negatives — below the spec's ~15% target,
because `AllergyIntolerance` covers only 96 records across 120 patients and
each patient contributes at most one negation candidate; broadening
`hw_open_allergy`'s negative branch would close most of the gap and is a
straightforward follow-up. 10 further questions (5.2%, all in train/dev by
chance of the patient-level split) are deliberately unanswerable
(`hand_written_ambiguous`) — see `docs/failure-analysis.md`'s `Q-AMBIG`
discussion.
