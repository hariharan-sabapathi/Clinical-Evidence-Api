"""PHI redaction before egress to a third-party model.

A rules layer, not Presidio: Presidio's useful accuracy comes from its
spaCy NER model (`en_core_web_lg`, several hundred MB), which needs a
model download this sandbox's network policy doesn't reliably allow and
which would make every test in tests/security/test_phi_egress.py depend
on that download succeeding. The spec explicitly allows either approach
"if it's tested" -- see docs/adr for the tradeoff. This covers the HIPAA
Safe Harbor identifier list that shows up in clinical free text: patient
and other person names (from the corpus's own patient roster, since we
know exactly who a note can mention), dates finer than year, phone,
email, SSN, MRN, and street addresses.

Redaction is deterministic and reversible within one call: the same name
always maps to the same pseudonym ("Patient A", "Person B", ...) so the
model can still reason about "Patient A" consistently across a single
prompt, and the mapping is handed back so the answer can be re-hydrated
with real names before it reaches the clinician -- the pseudonym never
needs to survive past one request/response cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}-\d{1,2}-\d{2,4})\b"
)
_PHONE_RE = re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_MRN_RE = re.compile(r"\bMRN[:#]?\s*\d{4,}\b", re.IGNORECASE)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\s]{1,30}\b(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|"
    r"Ct|Court|Way|Terrace|Ter|Circle|Cir|Place|Pl|Parkway|Pkwy|Highway|Hwy|Trail|Loop)\b\.?",
    re.IGNORECASE,
)
_AGE_OVER_89_RE = re.compile(r"\b(9\d|1\d{2})\s*(?:years?[\s-]old|y\.?o\.?)\b", re.IGNORECASE)


@dataclass
class RedactionResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # pseudonym -> original


class PHIRedactor:
    def redact(self, text: str, known_names: list[str] | None = None) -> RedactionResult:
        mapping: dict[str, str] = {}
        result = text

        result = _DATE_RE.sub(lambda m: _year_only(m.group(0)), result)
        result = _PHONE_RE.sub("[PHONE]", result)
        result = _EMAIL_RE.sub("[EMAIL]", result)
        result = _SSN_RE.sub("[SSN]", result)
        result = _MRN_RE.sub("[MRN]", result)
        result = _ADDRESS_RE.sub("[ADDRESS]", result)
        result = _AGE_OVER_89_RE.sub("[AGE]", result)

        for name in sorted(known_names or [], key=len, reverse=True):
            if not name or name in mapping.values():
                continue
            if re.search(re.escape(name), result):
                pseudonym = "Patient" if len(mapping) == 0 else f"Person-{chr(ord('B') + len(mapping) - 1)}"
                pseudonym = f"[{pseudonym}]"
                mapping[pseudonym] = name
                result = re.sub(re.escape(name), pseudonym, result)

        return RedactionResult(text=result, mapping=mapping)

    def rehydrate(self, text: str, mapping: dict[str, str]) -> str:
        result = text
        for pseudonym, original in mapping.items():
            result = result.replace(pseudonym, original)
        return result


def _year_only(date_str: str) -> str:
    match = re.search(r"\d{4}", date_str)
    return match.group(0) if match else "[DATE]"
