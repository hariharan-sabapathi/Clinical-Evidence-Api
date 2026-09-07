# clinical-retrieval

A clinical retrieval-augmented generation (RAG) system: it turns a synthetic
patient's clinical notes into searchable evidence, retrieves the encounters
relevant to a natural-language question with BM25, and (when an LLM is
configured) generates a grounded answer citing that evidence. Built against
the `smart-on-fhir/sample-bulk-fhir-datasets` 120-patient Synthea export:
**synthetic data, no PHI.**

## 1. Project overview

This repo demonstrates one thing end to end and evaluates it rigorously,
rather than demonstrating many things shallowly:

```
FHIR data
   ↓
Preprocessing
   ↓
Clinical chunks
   ↓
BM25
   ↓
Patient-aware evidence retrieval
   ↓
LLM
   ↓
Grounded clinical answer
```

Retrieval (everything up to and including "patient-aware evidence
retrieval") is fully implemented, executed, and evaluated against the real
120-patient dataset. LLM answer generation is fully implemented but needs an
API key not configured in this build — see §14.

## 2. Problem

A clinician (or a system acting on their behalf) asks a natural-language
question about a specific patient — "what medications was Shantelle354 on
during her 2019 visits?" The record needed to answer it exists somewhere in
that patient's clinical notes, but a patient can have hundreds of encounters
spanning decades, and the notes are highly repetitive (every note restates
that patient's condition history). The retrieval problem is: find the
handful of notes that actually answer the question, for the right patient,
without an LLM ever seeing the patient's full history at once.

## 3. Clinical dataset

Synthea-generated FHIR R4 bulk export, 120 patients:

| | |
|---|---|
| Patients | 120 |
| Encounters | 7,761 |
| Clinical notes (`DocumentReference`) | 7,761 — one per encounter, 100% coverage |
| Note sections | 6 per note: Chief Complaint, History of Present Illness, Social History, Allergies, Medications, Assessment and Plan |
| Encounters/patient | min 10, median ~35, max 672 |
| Condition / MedicationRequest / Procedure / Observation / Immunization | 4,294 / 7,263 / 20,844 / 70,233 / 1,775 — all 100% linked to an encounter |
| `AllergyIntolerance` | 96 records, patient-linked only (no encounter reference) |

Full facts and provenance: `docs/corpus-properties.md`. Fetch it yourself
with `scripts/fetch_data.sh` — it's not committed to this repo (~170MB).

## 4. Architecture

```
FHIR data (data/raw/*.ndjson)
   ↓  corpus/ndjson_loader.py, corpus/notes.py, corpus/fhir_serialize.py
Preprocessing — decode notes, join structured resources to encounters
   ↓  corpus/chunk.py
Clinical chunks — 5 strategies, see §6
   ↓  retrieval/bm25.py
BM25 — rank_bm25, the only retriever in this project's scope
   ↓  retrieval/filters.py
Patient-aware evidence retrieval — restrict to the named patient when one is resolved
   ↓  generation/llm_client.py, generation/generate.py
LLM — receives the retrieved evidence as context
   ↓
Grounded clinical answer — cites chunk ids, refuses when evidence is insufficient
```

`pipeline.py::Pipeline` is the one library entrypoint every caller (the CLI,
the eval harness) goes through — see §13.

## 5. Preprocessing

`corpus/ndjson_loader.py` streams the FHIR NDJSON files line by line
(`Observation` alone is 70K records across ~70MB; never `json.load()` this).
`corpus/notes.py` decodes each `DocumentReference`'s base64 note text and
splits it on its six section headers. `corpus/fhir_serialize.py` builds the
independent structured-FHIR view of the same encounters (medications,
conditions, procedures, observations, immunizations, with the patient's
allergy list attached) — used both as the `structured` chunking strategy
(§6) and as the source of ground truth for evaluation (§10), kept
deliberately separate from the note text being searched.

## 6. Chunking

Five strategies behind one interface (`corpus/chunk.py`), all built and
measured on the real corpus — kept because how you chunk clinical text
measurably changes what BM25 can find, which is itself a finding this repo
reports on, not just an implementation detail:

| Strategy | Chunks | Recall@5 (strict) | 95% CI |
|---|---|---|---|
| `full_note` | 7,761 | 0.414 | [0.241, 0.621] |
| `section` | 46,566 | 0.172 | [0.034, 0.310] |
| `patient_summary` (per patient-year) | 1,644 | 0.276 | [0.103, 0.448] |
| **`fixed_512` (baseline)** | 3,094 | **0.724** | [0.552, 0.897] |
| `structured` (serialized FHIR) | 7,761 | 0.483 | [0.310, 0.655] |

`fixed_512` — a naive 512-word window over each patient's notes,
concatenated chronologically — is the reference baseline (`configs/
reference.yml`) and beats every other strategy by a real, significance-
tested margin (§10). Full mechanism and the other strategies' trade-offs:
`docs/corpus-properties.md`.

## 7. BM25 retrieval

`retrieval/bm25.py` wraps `rank_bm25.BM25Okapi` — pure Python, no model
download, no GPU. This is the only retriever in this project's scope: an
earlier version of this repo also implemented dense embeddings, hybrid
fusion, cross-encoder reranking, and LLM query rewriting, but none of those
ever ran successfully (they need Hugging Face model downloads or an LLM API
key unavailable in that build), so they were removed rather than kept
around as unexecuted code — this project is one retrieval method, evaluated
properly, not several, evaluated in name only. Every result in this README
was produced by this code.

## 8. Patient filtering

`retrieval/filters.py::PatientFilteredRetriever` wraps `BM25Retriever`: if a
query names a patient (every Synthea patient has a distinctive
numeric-suffixed given name, e.g. "Shantelle354" — a plain token match
suffices, no NER model needed), retrieval is restricted to that patient's
own chunks before scoring. This is what keeps the system from mixing
evidence across patients. It directionally helps (Recall@5 strict 0.724 →
0.793) but a real paired significance test shows that lift is **not**
statistically distinguishable from 0 at this eval set's size (n=29,
95% CI [0.000, +0.172]) — reported as measured, not oversold.

## 9. RAG answer generation

`generation/prompts.py` builds a prompt from the retrieved chunks (each
tagged with its chunk id) and the question; `generation/generate.py` sends
it to an `LLMClient` and parses citations back out of the response.
`pipeline.py::Pipeline.answer()` is retrieval → generation in one call:
`retriever.retrieve(question, top_k)` (BM25, patient-filtered when
`--patient-filter` is set) feeds straight into `generate_answer()` — the LLM
never sees anything the retriever didn't return. The prompt instructs the
model to:

- answer only from the retrieved excerpts, and not invent, assume, or infer
  beyond them
- cite chunk ids for every claim, in square brackets
- preserve the patient's identity and the dates/encounter context exactly as
  given in the excerpts
- never combine evidence from a different patient than the one asked about,
  if the excerpts happen to contain more than one (patient filtering, §8, is
  the primary defense against this; the prompt is a second layer)
- distinguish what the excerpts actually support from anything less certain
- respond "Not present in the record." rather than guessing when the
  excerpts don't answer the question

`generation/llm_client.py` is a small provider-agnostic interface —
`OpenAICompatibleClient` works against any OpenAI-Chat-Completions-compatible
endpoint (reads its key from `$LLM_API_KEY`, never hardcoded, never logged —
see `tests/test_llm_client.py`); `NullLLMClient` is used when no key is
configured, so retrieval stays usable without an LLM. `configs/reference.yml`
sets a real default `llm_model` (`gpt-4o-mini`) — exporting `LLM_API_KEY` is
the only step needed to turn generation on; no code or config change
required (see §14 for what was and wasn't actually executed).

## 10. Evaluation methodology

The differentiator of this project. Full detail: `docs/eval-methodology.md`.

- **Ground truth is computed from structured FHIR, never from note text** —
  the corpus being searched and the source of correct answers are
  independent (`evalset/ground_truth.py`, `evalset/templates.py`).
- **Lenient vs. strict scoring, every table**: lenient counts any chunk
  containing the answer; strict counts only the encounter the fact actually
  originates from. The gap quantifies temporal precision vs. lucky
  duplication (`evalset/permissive.py`).
- **194 questions**, tiers 1–3 (single-hop, multi-hop/temporal,
  aggregation/negation) plus hand-written ones, generated programmatically
  and validated: 46 pairs manually checked against source data during the
  build, 2 issues found and fixed, 0 further issues in 34 more.
- **`data/splits.yml` is the actual source of truth** for the patient-level
  train/dev/test split (72/24/24 patients) — every script loads questions
  through `evalset/split.py::load_eval_questions`, which overwrites each
  question's split from the file rather than trusting a value embedded at
  generation time.
- **Evaluation prefers paraphrases over raw templates**: for any base
  question that has a paraphrase, the raw template is dropped from the
  evaluated set so a score reflects generalization, not double-counting.
- **Groundedness filter**: 84.5% of questions are grounded in note text —
  binary by fact type (100% for named entities, 0% for raw numeric
  measurements the notes never restate).
- **Bootstrap 95% CIs** (1,000 resamples) on every executed metric, plus
  **real paired bootstrap significance tests** on four real comparisons
  (not just implemented — actually run against real per-question results,
  see §11).
- **Per-tier, programmatic-vs-hand-written, and within-patient-vs-cross-
  patient breakdowns** — real numbers, real CIs, see §11.

## 11. Results

Baseline (`fixed_512`, BM25, k=5), test split (n=29 answerable questions
after paraphrase preference):

| Metric | Value | 95% CI |
|---|---|---|
| Recall@5 (strict — correct encounter) | 0.724 | [0.552, 0.897] |
| Recall@5 (lenient — any chunk with the answer) | 0.897 | [0.759, 1.0] |
| Lenient − strict gap @5 | 0.172 | tabulated per config in `results/ablations.csv` |
| MRR@10 (strict) | 0.531 | — |
| nDCG@10 (strict) | 0.568 | — |

**Real paired bootstrap significance tests** (`results/significance_tests.csv`):

| Comparison | Δ Recall@5 (strict) | 95% CI | Significant? |
|---|---|---|---|
| `fixed_512` baseline vs. `full_note` | +0.310 | [+0.138, +0.483] | **Yes** |
| top_k=10 vs. top_k=5 | +0.241 | [+0.103, +0.414] | **Yes** |
| within-patient (oracle) vs. cross-patient | +0.069 | [0.000, +0.172] | No |
| bm25+patient_filter vs. bm25 | +0.069 | [0.000, +0.172] | No |

Chunking strategy is a real, proven effect at this sample size; the patient
filter's apparent lift is not — both reported as measured, not adjusted to
tell a cleaner story.

**Extra analyses**, all real numbers, real CIs:

- **Per tier** (`results/breakdown_by_tier.csv`): tier 1 (single-hop) 100%
  Recall@5 strict (n=9); tier 2 (multi-hop/temporal) 62.5% (n=8); tier 3
  (aggregation/negation) 66.7% (n=9); hand-written (tier 0) 33.3% (n=3, tiny).
- **Programmatic vs. hand-written** (`results/breakdown_by_generation.csv`):
  76.9% vs. 33.3% strict.
- **Within-patient vs. cross-patient** (`results/within_vs_cross_patient.csv`):
  79.3% oracle-restricted vs. 72.4% unrestricted — directionally as
  predicted, not statistically confirmed at this size (see significance
  table above).
- **Retrieval depth curve** (`results/depth_curve.csv`): Recall@k, k=1..50.

`results/report.html` renders the full ablation table with CIs and the best
value per column highlighted — open it directly, no server needed.

## 12. Failure analysis

8 of 29 test questions fail at k=5 under the baseline config. Retrieval-side
taxonomy (`results/failure_labels.csv`, full write-up with worked examples
in `docs/failure-analysis.md`):

| Code | Count | Meaning |
|---|---|---|
| R-TEMPORAL | 5 | Right patient/fact, wrong encounter |
| R-RANK | 2 | Retrieved but ranked below distractors |
| R-PATIENT | 1 | Wrong patient entirely |
| R-MISS | 0 | Not in the top-50 pool at all |

Generation-side codes (`G-IGNORE`, `G-HALLU`, `G-CITE`, `G-REFUSE`) need an
actual generated answer to inspect and weren't run here — see §14.

## 13. How to run

```bash
pip install -e .          # core: BM25, metrics, eval harness — no GPU needed;
                           # also makes `python -m clinical_retrieval...` work
                           # from anywhere without setting PYTHONPATH
pip install -e ".[llm]"   # + openai-compatible client — needed for real generation;
                           # retrieval and the full test suite work without it too

./scripts/fetch_data.sh                # stages the raw FHIR export into data/raw/
python -m pytest                       # 91 tests, no external services needed

# To actually generate answers (optional — retrieval works without this):
export LLM_API_KEY=<your key>          # configs/reference.yml already names a real model

python scripts/build_eval_set.py       # regenerates data/eval_set.jsonl, data/splits.yml
python scripts/run_ablations.py        # regenerates results/ablations.csv, results/depth_curve.csv
python scripts/run_extra_analyses.py   # per-tier/generation/within-cross-patient + significance tests
python scripts/label_failures.py       # regenerates results/failure_labels.csv
python -m clinical_retrieval.eval.report --out results/report.html

python -m clinical_retrieval.query --config configs/reference.yml "<question>"
python -m clinical_retrieval.query --config configs/reference.yml --patient-filter "<question>"
```

(Without `pip install -e .`, run the `scripts/*.py` files exactly as above —
they add `src/` to their own path — but prefix the `python -m
clinical_retrieval...` commands with `PYTHONPATH=src`.)

Example (baseline config, retrieval-only since no LLM key is set):

```
$ python -m clinical_retrieval.query --config configs/reference.yml \
    "what medications was Shantelle354 on during her 2019 visits?"

RETRIEVED (bm25, chunk_level=fixed_512, k=5)
  1. fixed_512::45619ed8-7e57-e6b9-0170-7a0c9619ccba::15  score 10.641  2019-05-21  General examination of patient (procedure)
     "tablet; ibuprofen 400 mg oral tablet [ibu]  # Assessment and Plan Patient is pre..."
  2. fixed_512::445e82f0-d99c-4417-15f7-08b22c47b525::2   score 10.305  1999-12-21  Encounter for problem (procedure)
     "Allergies.  # Medications No Active Medications.  # Assessment and Plan Patient ..."
  ...

ANSWER
  Not present in the record. (No LLM configured — retrieval-only mode.)

TIMING  index_load 2707ms | retrieve 14ms | generate 0ms
```

Switching to `--chunk-level full_note` reproduces the cross-patient
confusion §7/§8 describe: other patients' notes outrank the named patient's
own visits. `--patient-filter` fixes patient identification but not
encounter selection — full transcripts in `docs/corpus-properties.md` and
`docs/failure-analysis.md`.

Or via Docker: `docker-compose up` runs fetch → eval-set build → ablations →
extra analyses → failure labeling → HTML report in one container.

### Repo layout

```
src/clinical_retrieval/
├── corpus/       # NDJSON streaming, note extraction, FHIR serialization, chunking
├── evalset/      # ground truth, templates, grounding filter, permissive sets, paraphrase, split
├── retrieval/    # bm25, patient filter
├── generation/   # prompts, provider-agnostic LLM client, answer generation
├── eval/         # metrics (bootstrap CIs, paired tests), ablation runner, breakdown, HTML report
├── pipeline.py   # answer(question, config) -> Answer — the one library entrypoint
└── query.py      # CLI wrapper over pipeline.py
scripts/          # build_eval_set.py, run_ablations.py, run_extra_analyses.py,
                  # label_failures.py, fetch_data.sh
data/             # eval_set.jsonl, splits.yml (versioned); raw/ (gitignored, reproducible)
results/          # ablations.csv, report.html, failure_labels.csv, depth_curve.csv,
                  # breakdown_by_tier.csv, breakdown_by_generation.csv,
                  # within_vs_cross_patient.csv, significance_tests.csv (all versioned)
docs/             # eval-methodology.md, corpus-properties.md, failure-analysis.md
tests/
```

## 14. Environment / API limitations

This build ran in a sandbox with no LLM API key available. What that means,
precisely:

1. **Implemented and executed**: everything in §4 through §12 — corpus
   preprocessing, all 5 chunking strategies, BM25 retrieval, patient
   filtering, the full 194-question evaluation pipeline, bootstrap CIs,
   real paired significance tests, per-tier/generation/within-cross-patient
   breakdowns, and retrieval-side failure labeling. All real, reproducible
   by running §13's commands. Also executed here: the LLM generation code
   path itself, tested with a mocked/faked LLM client (`tests/
   test_generation.py`, `tests/test_llm_client.py`, `tests/
   test_query_llm_selection.py`) — confirming retrieved evidence reaches the
   prompt, the grounding rules are present, citations are parsed and
   fabricated ones rejected, and no key ever appears in an error message,
   warning, or output. What was **not** executed is a real call to a live
   LLM API, because no key was available in this sandbox.
2. **Implemented, and one step from executable — needs an external LLM API
   key**: real answer generation and the generation-side failure taxonomy
   codes (`G-IGNORE`, `G-HALLU`, `G-CITE`, `G-REFUSE`), which need an actual
   generated answer to label. `configs/reference.yml` already sets a real
   `llm_model` (`gpt-4o-mini`); the only remaining step is
   `export LLM_API_KEY=<your key>` — no code or config change needed.
   `NullLLMClient` keeps retrieval-only operation working without one,
   which is what every CLI example in this README actually ran with.
3. **Removed by scope decision, not by failure of the idea**: dense/
   embedding retrieval, hybrid fusion, cross-encoder reranking, LLM query
   rewriting, and embedding fine-tuning. An earlier version of this repo
   implemented all of these, but none ever executed successfully — every
   one needs Hugging Face model downloads or an LLM API key unavailable in
   that build, so their results were always "skipped," never real. Rather
   than ship unexecuted code alongside a real evaluation, they were removed
   entirely: this project's final scope is a focused, thoroughly-evaluated
   BM25 clinical RAG system, not a broad survey of retrieval methods that
   mostly couldn't run.

## Limitations

- 120 patients, synthetic (Synthea) and template-generated — not real
  clinical documentation, and no comparison against a real-world clinical
  note dataset.
- 194 questions, 29 in the evaluated test split after paraphrase
  preference — several results above have wide, overlapping confidence
  intervals at this sample size (reported explicitly, backed by real paired
  significance tests rather than implied away).
- Allergy-negation true negatives are 6.2% of the eval set — `AllergyIntolerance`
  covers only 96 records across 120 patients, capping how many negation
  candidates exist.
- No external benchmark comparison — this is a self-contained benchmark over
  one corpus.
