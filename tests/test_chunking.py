from clinical_retrieval.corpus.notes import _split_sections, SECTION_ORDER

SAMPLE_NOTE = """2005-10-20

# Chief Complaint
- Thirst

# History of Present Illness
Some history.

# Social History
Married.

# Allergies
No Known Allergies.

# Medications
metformin

# Assessment and Plan
Plan text.

## Plan
Nested subsection that must stay inside Assessment and Plan.
"""


def test_split_sections_finds_all_six_canonical_sections():
    sections = _split_sections(SAMPLE_NOTE)
    for name in SECTION_ORDER:
        assert name in sections, f"missing section {name}"


def test_nested_subheading_does_not_split_out_as_its_own_section():
    sections = _split_sections(SAMPLE_NOTE)
    assert "Plan" not in sections  # would appear if '##' were treated as a top-level header
    assert "Nested subsection" in sections["Assessment and Plan"]


def test_chief_complaint_body_excludes_the_header_line():
    sections = _split_sections(SAMPLE_NOTE)
    assert "Chief Complaint" not in sections["Chief Complaint"]
    assert "Thirst" in sections["Chief Complaint"]
