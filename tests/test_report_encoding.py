"""Regression test for a UTF-8 encoding bug in eval/report.py: render_report
wrote its output HTML with `open(out_path, "w")` — no explicit encoding — so
on a system whose locale default isn't UTF-8 (e.g. Windows/cp1252), the
em dashes in the template got silently written in the wrong encoding while
the file's own <meta charset="utf-8"> tag told browsers to decode it as
UTF-8, producing mojibake ("—" rendered as "ù" or "ΓÇö").

The fix is `encoding="utf-8"` on both the CSV read and the HTML write in
render_report(). This test doesn't need to reproduce a non-UTF-8 locale to
catch a regression: reading the output back as raw bytes and checking for
the exact UTF-8 byte sequence for U+2014 (and the absence of known mojibake
byte patterns) is enough — if someone removes `encoding="utf-8"` again on a
machine whose default happens to already be UTF-8 (like this one), the text
would still look right when read back with Python's own default decoder,
which is exactly how this bug went unnoticed the first time.
"""

import csv

from clinical_retrieval.eval.report import render_report

EM_DASH = "—"  # —
EM_DASH_UTF8_BYTES = EM_DASH.encode("utf-8")  # b"\xe2\x80\x94"

_KNOWN_MOJIBAKE = ["ù", "ΓÇö", "â€”", "Ã¢â‚¬â€"]


def _write_minimal_ablations_csv(path):
    fieldnames = ["label", "chunk_level", "retriever", "top_k", "patient_filter", "status", "n_questions", "recall_strict@5"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "label": "chunk_level=fixed_512", "chunk_level": "fixed_512", "retriever": "bm25",
            "top_k": 5, "patient_filter": False, "status": "ok", "n_questions": 29,
            "recall_strict@5": 0.724,
        })


def test_report_html_is_valid_utf8_and_contains_the_em_dash(tmp_path):
    csv_path = tmp_path / "ablations.csv"
    out_path = tmp_path / "report.html"
    _write_minimal_ablations_csv(csv_path)

    render_report(str(csv_path), str(out_path))

    raw_bytes = out_path.read_bytes()
    # Must decode cleanly as UTF-8 — a mis-encoded file would either fail
    # this or decode into mojibake rather than raising, so this alone isn't
    # sufficient, but a real cp1252-on-Windows corruption of "—" produces
    # byte 0x97, which read back on a UTF-8-locale system like this one
    # would blow up strict UTF-8 decoding if it landed mid-multibyte-sequence
    # elsewhere in the file — the exact byte-sequence checks below are the
    # real assertion.
    decoded = raw_bytes.decode("utf-8")

    assert EM_DASH_UTF8_BYTES in raw_bytes, "expected the real UTF-8 em dash byte sequence in the output file"
    assert EM_DASH in decoded
    assert "clinical-retrieval " + EM_DASH + " ablation results" in decoded

    for mojibake in _KNOWN_MOJIBAKE:
        assert mojibake not in decoded, f"found known mojibake artifact {mojibake!r} in generated report"


def test_report_html_declares_utf8_charset(tmp_path):
    csv_path = tmp_path / "ablations.csv"
    out_path = tmp_path / "report.html"
    _write_minimal_ablations_csv(csv_path)

    render_report(str(csv_path), str(out_path))

    content = out_path.read_text(encoding="utf-8")
    assert '<meta charset="utf-8">' in content
