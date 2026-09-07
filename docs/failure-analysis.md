# Failure analysis

`scripts/label_failures.py` labels every test-split question the reference
config (the §4.1 baseline: `fixed_512`, bm25, k=5 — `configs/reference.yml`)
fails at k=5, using `eval/failure_taxonomy.py`'s automatic classifier for
the retrieval-side codes. Evaluated on the paraphrase-preferred question set
(§3.3 — see `docs/eval-methodology.md`). Results: `results/failure_labels.csv`.

**8 of 29 test questions failed at k=5** (matching the baseline config's
measured Recall@5 strict of 0.724 — see `docs/corpus-properties.md`).

| Code | Count | Description |
|---|---|---|
| `R-TEMPORAL` | 5 | Right patient and fact, wrong encounter |
| `R-RANK` | 2 | Retrieved but ranked below distractors |
| `R-PATIENT` | 1 | Retrieved the wrong patient entirely |
| `R-MISS` | 0 | Gold chunk not in top-50 pool at all |
| `Q-AMBIG` | 0 in test split (10 exist in train/dev — see below) | Question genuinely ambiguous |
| `G-*` (generation-side) | not run | needs an LLM — see README "Environment constraints" |

**This updates the a priori prediction in the build spec.** §1.1 expected
`R-RANK` to dominate over `R-MISS` — that part holds (`R-MISS` is exactly
zero: BM25's pool of 50 candidates always contains *something* relevant on
this corpus). But once `R-PATIENT` is checked *before* falling back to
`R-RANK` — i.e., a majority-wrong-patient top-5 is labeled as a
patient-identification failure even if the right patient's chunk exists
somewhere deeper in the pool of 50 — the largest category is `R-TEMPORAL`
(5 of 8, 62.5%): the retriever gets the right *patient* and often the right
*fact*, but returns a different encounter than the one the fact actually
originates from. That's a sharper version of the spec's own §1.1/§1.2 story:
cross-encounter leakage doesn't just inflate lenient-vs-strict scores in
aggregate, it's the literal, dominant, individual failure mode when you look
at specific misses. At n=8 this is a small sample — the proportions should
be read as directional, not precise.

(An earlier version of the classifier checked `R-PATIENT` only when the gold
chunk was missing from the *entire* 50-chunk pool, which meant a top-5 that
happened to be dominated by the wrong patient — while the right patient's
chunk existed further down the pool — was labeled `R-RANK` instead.
Reordering the check to look at what the top-k actually shows a user, before
falling back to pool-membership, is the version reported above.)

## Worked examples

### R-TEMPORAL

> **Q:** What's the most recent thing Allan198 Federico589 Leffler128 was
> treated for?
> **Reference answer:** Primary dental caries (disorder)

Top-5 retrieved are all five `fixed_512` windows for the correct patient
(`c83a13c7-...`) — patient identification succeeded — but the gold window
(`fixed_512::c83a13c7-9f5a-ccb3-1aa9-6d2678e41125::6`, a later window in the
same patient's chronological note stream) isn't among them. BM25 has no
temporal or recency signal at all; it ranks by lexical overlap with the
query's terms, which — per §1.1 — recur across nearly every window this
patient has, since notes restate condition history cumulatively.

### R-RANK

> **Q:** What is the trend in Kara173 Teresia279 Hodkiewicz467's Hemoglobin
> A1c over their documented visits?
> **Reference answer:** Hemoglobin A1c stayed roughly stable from 6.2% on
> 2013-10-18 to 6.4% on 2022-12-22.

Same patient throughout the top-5 (`8c4baf5a-...`), so patient
identification isn't the issue — but the three gold windows (containing the
actual lab values across the patient's visit history) are ranked below
distractor windows from the same patient. This is a lab-trend question,
which §3.2's grounding analysis already flags as a fact type numeric values
rarely restate verbatim in note text — a genuinely hard case for a lexical
retriever even *within* the right patient.

### R-PATIENT

> **Q:** Has Allan198 Federico589 Leffler128 ever had a documented allergy
> to Grass pollen?
> **Reference answer:** No — Allan198 Federico589 Leffler128 has no
> documented allergies.

All five top-5 results are from *other* patients entirely — none share
`Allan198`'s patient id (`c83a13c7-...`). Allergy-negation questions are
answered by the *absence* of a specific term, so there's no distinctive
positive lexical signal beyond the patient's own name to anchor BM25 to the
right patient — a structurally harder case than a positive-fact lookup, and
worth flagging as a property of this specific question type rather than a
generic BM25 weakness.

### Q-AMBIG (from train/dev — none landed in the test split this run)

> **Q:** How has Esperanza675 So684 Erdman779 been doing lately?
> **Answer:** Ambiguous — no timeframe or dimension (a condition, a lab,
> overall status) is specified.

10 questions in the eval set are deliberately unanswerable by design
(`hand_written_ambiguous`, `evalset/hand_written.py`) — vague or
underspecified natural questions a real user might actually ask, with no
single correct retrieval target. None happened to fall in the test split
under this run's patient-level stratified split (they're concentrated in a
few patients that landed in train/dev). They exist to exercise `Q-AMBIG` in
the taxonomy and are called out as an eval-set property rather than
excluded, per the spec's point that this is itself a credibility signal.

## What I'd fix next

Ranked by the size of the category it addresses:

1. **R-TEMPORAL (5 failures) — add date-aware scoring.** Nothing in this
   repo's BM25 baseline uses `encounter_date`. Even a simple date-proximity
   boost (parse a date/year mentioned in the query, penalize chunks whose
   `encounter_date` is far from it) would directly target the largest
   category, and costs nothing to implement — it doesn't require a model.
2. **R-RANK / R-TEMPORAL together — a learned reranker.** A cross-encoder
   reranking BM25's top candidates would plausibly help here more than
   usual, precisely because BM25 barely discriminates within a patient. This
   project's scope is deliberately BM25-only (see README "Architecture"), so
   reranking isn't implemented — noted here as the most likely next lever if
   the scope were extended, not as something partially built.
3. **R-PATIENT (1 failure) — apply the patient filter, but note it isn't a
   proven win yet.** `retrieval/filters.py`'s `PatientFilteredRetriever`
   directionally helps (Recall@5 strict 0.724 → 0.793 with it on, per
   `results/ablations.csv`), but the real paired significance test in
   `results/significance_tests.csv` puts that difference at 95% CI
   [0.000, +0.172] — not distinguishable from 0 at n=29. Worth turning on by
   default once a larger eval set can confirm the effect, not before.
4. **`section`-level chunking's collapse (Recall@5 0.172) — carry patient/
   date context into every section chunk.** Diagnosed in
   `docs/corpus-properties.md` §1: an isolated `Medications` section chunk
   has no lexical anchor back to the patient. Prepending a one-line header
   (patient name, date) to each section chunk's text before indexing would
   directly test this.
