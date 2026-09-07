"""§3.3 paraphrase augmentation.

The spec calls for LLM-generated paraphrases, verified by hand. This sandbox
has no reachable LLM API (see README "Environment constraints"), so this is a
documented substitute: a rule-based paraphraser — phrasing-pattern swaps plus
a small clinical-question synonym table — applied to each base template
question. It buys real lexical/structural variation (the point of §3.3: don't
evaluate on surface forms a retriever could memorize) without an external
model. It is not a substitute for LLM paraphrase diversity, and that gap is
called out in docs/eval-methodology.md rather than papered over.

A sample of the output was read back against its base question during the
build to check the paraphrase preserved meaning; see the human-validation
note in docs/eval-methodology.md for the checked count and error rate.
"""

from __future__ import annotations

import re

from ..common.types import EvalQuestion

_SYNONYMS = [
    (r"\bWhat medications was\b", "Which drugs was"),
    (r"\bwas prescribed\b", "was given"),
    (r"\bWhat was\b", "Can you tell me what"),
    (r"\bdiagnosed with\b", "found to have"),
    (r"\bWhich conditions\b", "What conditions"),
    (r"\bafter starting\b", "once they began"),
    (r"\bHow many times\b", "On how many occasions"),
    (r"\bwas seen for\b", "had a visit for"),
    (r"\bHas\b(.+)\bever had a documented allergy to\b", r"Does\1have any known allergy to"),
    (r"\bWhat is the trend in\b", "How has"),
    (r"\bover their documented visits\?", "changed across their visits?"),
    (r"\bWhat immunization did\b", "Which vaccine did"),
    (r"\breceive on\b", "get on"),
    (r"\bIn total, how many times has\b", "Overall, how many times has"),
    (r"\bmost recently before being diagnosed with\b", "right before they were diagnosed with"),
]

def paraphrase(question: str) -> str:
    """Synonym/phrasing substitution only — an earlier version also tried a
    structural "front the date clause" rewrite via regex, but patient names in
    this corpus are themselves multi-word ("Don899 Eliseo499 Swift555"),
    which made a lazy-match regex split the name from the verb at the wrong
    word and produce ungrammatical output. Substitution-only is less dramatic
    variation but never breaks grammar, which matters more for a paraphrase
    a human is meant to spot-check (§3.3)."""
    text = question
    for pattern, repl in _SYNONYMS:
        text = re.sub(pattern, repl, text)
    return text


def make_paraphrases(base_question: dict, base_qid: str, n: int = 1) -> list[dict]:
    out = []
    variant = paraphrase(base_question["question"])
    if variant.strip().lower() == base_question["question"].strip().lower():
        return []
    para = dict(base_question)
    para["question"] = variant
    para["generation"] = "paraphrase"
    para["paraphrase_of"] = base_qid
    out.append(para)
    return out[:n]


def prefer_paraphrases(questions: list[EvalQuestion]) -> list[EvalQuestion]:
    """§3.3: "evaluate on paraphrases rather than raw templates." For every
    base programmatic question that has a paraphrase, drop the raw template
    from the evaluated set and keep only its paraphrase; questions with no
    paraphrase (most hand-written ones, and any programmatic question that
    wasn't sampled for paraphrasing) pass through unchanged. This is what
    eval/runner.py and scripts/label_failures.py should run on, not the raw
    eval_set.jsonl contents — evaluating both a template and its paraphrase
    would double-count that fact in a metric and defeats the point (testing
    whether the retriever generalizes past a surface form it could have
    memorized, not just repeating the check twice)."""
    paraphrased_bases = {q.paraphrase_of for q in questions if q.paraphrase_of}
    return [q for q in questions if q.qid not in paraphrased_bases]
