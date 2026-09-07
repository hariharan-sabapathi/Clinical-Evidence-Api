# Corpus properties

This project is built against the `smart-on-fhir/sample-bulk-fhir-datasets`
120-patient Synthea export (the branch is named `100-patients` after the
Synthea generation run size; the actual output contains 120 patients — see
`scripts/fetch_data.sh`). All numbers below were measured against the real
data in this repo's pipeline, not estimated.

| | |
|---|---|
| Patients | 120 |
| Encounters | 7,761 |
| Clinical notes (`DocumentReference`) | 7,761 — one per encounter, 100% coverage |
| Note sections | 6 per note: Chief Complaint, History of Present Illness, Social History, Allergies, Medications, Assessment and Plan — all 7,761 notes parse cleanly into exactly these six |
| Note length | median ≈1,490 chars |
| Encounters/patient | min 10, median ~35 (varies), max 672 |
| `AllergyIntolerance` | 96 records, patient-linked only (no `encounter` reference) |

Encounter linkage confirmed at 100% for Condition (4,294), MedicationRequest
(7,263), Procedure (20,844), Observation (70,233 across two ~50MB/20MB
files), DiagnosticReport (15,360, of which 7,761 duplicate the narrative note
as a `presentedForm` attachment and are excluded from the `structured`
chunking variant to keep it a fair comparison), Immunization (1,775).

## 1. Template homogeneity

Every note shares the same six headers, and `History of Present Illness`
restates a patient's **entire** condition history on every visit — a note
from 2020 lists conditions diagnosed in 2013. Notes from the same patient are
therefore near-duplicates at the lexical level.

This is directly visible in the CLI demo (`README.md` "Try it"): a BM25 query
naming a patient returns results from *other* patients ranked above that
patient's own most relevant visit, and even after restricting to the named
patient with `--patient-filter`, the top hits are from 1982, 1999, and 2001 —
not the 2019 visits actually asked about. BM25 has no way to distinguish one
of a patient's visits from another when the query's only real anchor (the
patient's name) appears identically in every one of their notes.

**Chunk-level effect on retrieval**, measured on the test split (n=29
answerable questions after paraphrase preference, §3.3 — bm25, k=5,
Recall@5 strict/gold-exact):

| chunk_level | Recall@5 (strict) | 95% CI |
|---|---|---|
| `section` | 0.172 | [0.034, 0.310] |
| `patient_summary` | 0.276 | [0.103, 0.448] |
| `full_note` | 0.414 | [0.241, 0.621] |
| `structured` | 0.483 | [0.310, 0.655] |
| **`fixed_512` (baseline, §4.1)** | **0.724** | [0.552, 0.897] |

`results/ablations.csv` has the full bootstrap CIs for every row, plus a
`lenient_minus_strict@k` column for every k — and `results/
significance_tests.csv` has a real paired bootstrap test confirming
`fixed_512` beats `full_note` at this sample size (Δ +0.310, 95% CI
[+0.138, +0.483], significant), so this isn't just a point-estimate ranking
that a bigger sample could reverse — see `docs/eval-methodology.md`.

`section` chunking is the clear loser, for a reason worth stating explicitly:
splitting a note into its six sections also splits out the patient's name,
which almost always lives in `History of Present Illness`, not in
`Medications` or `Allergies`. A query like "What medications was Shantelle354
prescribed..." shares no lexical overlap with an isolated `Medications`
section chunk that reads "tropicamide 5 mg/ml...; metformin hydrochloride...”
— the name that anchors the query to the right patient simply isn't in that
chunk's text. This is a second, independent mechanism (beyond §1.1's general
homogeneity point) by which chunking strategy interacts with how BM25 can
match at all, and it argues for carrying patient/date context into every
section chunk rather than splitting it out cleanly — a concrete "what I'd fix
next" item (see `docs/failure-analysis.md`).

`fixed_512` scoring *highest* here is a genuine, slightly counter-intuitive
finding, not the "baseline to beat" its usual framing implies for this
corpus: because it concatenates a patient's notes chronologically before
windowing, temporally adjacent encounters (e.g., a medication and the
condition diagnosed shortly after it) often land in the *same* window,
which trivially satisfies multi-hop questions that need both facts. That's a
real property of naive fixed-size chunking on a chronological corpus, and
it's reported here rather than re-tuned away.

### Within-patient vs. cross-patient retrieval (§6.5)

`scripts/run_extra_analyses.py` operationalizes §1.1's claim directly:
"cross-patient" = unrestricted BM25 over the whole corpus (what every number
above measures); "within-patient" = the *same* queries, but retrieval is
given an oracle restriction to the question's own correct `patient_id`
before scoring — isolating "can BM25 pick the right encounter, given the
patient is already known" from "can BM25 find the patient at all."

| | Recall@5 (strict) | Recall@5 (lenient) |
|---|---|---|
| cross-patient (unrestricted) | 0.724 | 0.897 |
| within-patient (oracle) | 0.793 | 0.931 |

Within-patient is higher, which fits §1.1's prediction directionally — but a
real paired bootstrap test on these exact per-question results (`results/
significance_tests.csv`) puts the difference at +0.069, 95% CI
[0.000, +0.172]: **not distinguishable from 0** at this sample size (n=29).
The honest statement is "directionally consistent with the prediction, not
proven" — not "confirmed," which is what reporting only the point estimate
would have implied.

## 2. Cross-encounter answer leakage

Because history is cumulative, a fact is often present in many chunks for
the same patient, not just the one where the event happened. This project
reports **Recall@k (lenient)** — any chunk containing the answer counts — and
**Recall@k (strict)** — only the encounter where the event actually
occurred — in every table (see `docs/eval-methodology.md` §1.2 and
`evalset/permissive.py`).

The gap is large and it is the point: for the baseline config
(fixed_512, bm25, k=5, test split), lenient Recall@5 is **0.897** against a
strict Recall@5 of **0.724** (95% CI [0.552, 0.897] — n=29, so treat the
point estimate cautiously; see `results/ablations.csv`). The gap is even
larger for `full_note` (0.724 lenient − 0.414 strict = 0.310) than for
`fixed_512` (0.172) — chunking strategy changes how much temporal-precision
gap exists, not just the raw recall level; the full per-config table is the
`lenient_minus_strict@k` columns in `results/ablations.csv`. A permissive set
for a single multi-encounter question can run into the hundreds of chunks —
e.g. `t2_0029_p1` ("conditions found after starting Epoetin Alfa") has 475
permissive chunk ids for one patient, because the medication and every
resulting condition are each restated across dozens of that patient's later
notes. Roughly half of the system's apparent performance at k=5 is temporal
precision, not raw information retrieval.

## 3. `patient_summary` sub-chunking

A single blob per patient would be degenerate (120 chunks turns retrieval
into "find the right patient," trivial when the patient is named) and one
672-encounter patient would produce an unworkable ~1M-character chunk.
Sub-chunked by (patient, year) instead: 120 patients →
see `data/chunks/patient_summary.jsonl` for the exact count (varies by run
seed only in eval-set sampling, not corpus construction, so this is stable).
Each year-chunk concatenates that year's encounters' Chief Complaint +
Assessment and Plan sections only — not the full note — specifically to
avoid compounding the cumulative-history redundancy from §1 rather than
multiplying it across a whole year of visits.

## 4. `structured` vs. narrative

`corpus/fhir_serialize.py` serializes each encounter's linked Condition,
MedicationRequest, Procedure, Observation (non-note DiagnosticReport),
and Immunization resources into readable text, with the patient's
AllergyIntolerance list prepended (since it has no encounter link, every
narrative note also repeats it — the structured variant mirrors that so the
two are a fair comparison over the same encounters and questions). Code
displays (SNOMED/LOINC/RxNorm) are read directly from each `coding[].display`
already present in this export — no external terminology service needed.

`structured` outperforms `full_note` narrative retrieval at k=5
(0.483 vs. 0.414, strict) with overlapping CIs at this sample size ([0.310,
0.655] vs. [0.241, 0.621]) — a real-looking but statistically unconfirmed
gap, reported as such rather than rounded into a headline (no paired
significance test was run for this specific pair; see `results/
significance_tests.csv` for the pairs that were). The structured text is
denser per character (no restated HPI paragraph, no boilerplate), which
plausibly helps BM25's term-frequency scoring; a larger eval set would be
needed to say more.
