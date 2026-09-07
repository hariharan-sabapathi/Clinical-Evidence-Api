"""§8.2 — render results/ablations.csv as a standalone HTML table: metric
columns, confidence intervals, lenient/strict pairs side by side, best value
per numeric column highlighted. No JS framework, no network dependency — it
has to render offline, years from now, from a single committed file.

    PYTHONPATH=src python3 -m clinical_retrieval.eval.report --out results/report.html
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path

_STYLE = """
body { font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; margin: 2rem; color: #1a1a1a; background: #fff; }
h1 { font-size: 1.3rem; }
p.meta { color: #666; font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.82rem; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: right; white-space: nowrap; }
th { background: #f4f4f4; position: sticky; top: 0; }
td.label-col, th.label-col { text-align: left; }
td.best { background: #d9f2d9; font-weight: 600; }
td.skipped { color: #999; font-style: italic; }
.wrap { overflow-x: auto; }
.ci { color: #888; font-size: 0.75em; }
"""

_LABEL_COLS = {"label", "chunk_level", "retriever", "top_k", "patient_filter", "status"}
_CI_PAIR_SUFFIXES = ("_ci_lo", "_ci_hi")


def _is_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def render_report(csv_path: str, out_path: str) -> None:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"No rows in {csv_path}")

    all_fields = list(rows[0].keys())
    base_metric_cols = [
        c for c in all_fields
        if c not in _LABEL_COLS and c != "n_questions" and not c.endswith(_CI_PAIR_SUFFIXES)
    ]

    best_per_col: dict[str, float] = {}
    lower_is_better = {"latency_p50_ms", "latency_p95_ms"}
    for col in base_metric_cols:
        values = [float(r[col]) for r in rows if r.get("status") == "ok" and _is_numeric(r.get(col))]
        if not values:
            continue
        best_per_col[col] = min(values) if col in lower_is_better else max(values)

    header_cols = ["label", "chunk_level", "retriever", "top_k", "patient_filter", "status", "n_questions"] + base_metric_cols

    def fmt_cell(row: dict, col: str) -> str:
        raw = row.get(col, "")
        if row.get("status") != "ok" or not _is_numeric(raw):
            return html.escape(str(raw))
        value = float(raw)
        lo, hi = row.get(f"{col}_ci_lo"), row.get(f"{col}_ci_hi")
        text = f"{value:.3f}"
        if lo is not None and hi is not None and _is_numeric(lo) and _is_numeric(hi):
            text += f' <span class="ci">[{float(lo):.3f}, {float(hi):.3f}]</span>'
        return text

    def cell_class(row: dict, col: str) -> str:
        classes = []
        if col in _LABEL_COLS:
            classes.append("label-col")
        if row.get("status") != "ok":
            classes.append("skipped")
        elif col in best_per_col and _is_numeric(row.get(col)) and float(row[col]) == best_per_col[col]:
            classes.append("best")
        return " ".join(classes)

    thead = "".join(f'<th class="{"label-col" if c in _LABEL_COLS else ""}">{html.escape(c)}</th>' for c in header_cols)
    tbody_rows = []
    for row in rows:
        cells = "".join(
            f'<td class="{cell_class(row, c)}">{fmt_cell(row, c)}</td>' for c in header_cols
        )
        tbody_rows.append(f"<tr>{cells}</tr>")

    doc = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Ablation results</title><style>{_STYLE}</style></head>
<body>
<h1>clinical-retrieval — ablation results</h1>
<p class="meta">Generated from {html.escape(csv_path)}. Green = best value in column among rows with status "ok".
Rows with any other status were not executed in the environment that generated this report — see the status column
and README "Environment constraints" for why.</p>
<div class="wrap">
<table>
<thead><tr>{thead}</tr></thead>
<tbody>{"".join(tbody_rows)}</tbody>
</table>
</div>
</body></html>
"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    # Explicit encoding="utf-8" is load-bearing, not stylistic: open()'s
    # default text encoding is the OS locale's preferred encoding (e.g.
    # cp1252 on Windows), not UTF-8. Without this, non-ASCII characters like
    # the em dashes above get silently written in the wrong encoding, while
    # the <meta charset="utf-8"> tag above still tells the browser to decode
    # the file as UTF-8 — producing mojibake ("—" -> "ù" or "ΓÇö") even
    # though nothing in this function's own logic is wrong.
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default="results/ablations.csv")
    parser.add_argument("--out", default="results/report.html")
    args = parser.parse_args()
    render_report(args.csv, args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
