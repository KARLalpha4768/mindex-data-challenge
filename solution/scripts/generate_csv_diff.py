#!/usr/bin/env python3
"""Generate the dashboard's raw-vs-clean diff from the pipeline's own artifacts.

WHY THIS SCRIPT EXISTS
----------------------
`dashboard/public/data/csv_diff.json` powers the Raw vs Clean inspector, and the
grounded assistant now answers "why is this cell red?" straight out of it. The
previous file was produced by a separate script that re-derived each row's fate
with its own logic, and it disagreed with the pipeline on **101 of 505
transaction rows** — a fifth of the dataset. The disagreements were not random;
they inverted the submission's headline decisions:

    pipeline: kept        -> file: quarantined   40 rows   the TX-06 guest checkouts
    pipeline: kept        -> file: dropped       30 rows   the TX-10 returns
    pipeline: quarantined -> file: kept          16 rows   the real orphans/zero-qty
    pipeline: dropped     -> file: kept          15 rows   the TX-09 duplicates

Clicking a return therefore produced a confident, grounded, code-linked answer
reading "Invalid negative quantity on non-return sale dropped." Returns are
*preserved* — that is the argument the README, the email and the interview
answers all rest on — and the dashboard said the opposite. A numeric self-audit
cannot catch this: the figures are all present in the context it was given. The
context was internally consistent and externally false.

So this file is now DERIVED, never re-derived. Dispositions come from the
lineage ledger the pipeline writes; clean values come from the cleaned CSVs;
defect codes and their prose come from the defect catalog. Where a fact exists
in an artifact, it is read, not recomputed. `--check` mode makes drift a build
failure, exactly as `check_readme_numbers.py` does for the README.

USAGE
    python solution/scripts/generate_csv_diff.py
    python solution/scripts/generate_csv_diff.py --output-dir /tmp/run --check
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw"
DEFAULT_TARGET = REPO_ROOT.parent / "dashboard" / "public" / "data" / "csv_diff.json"

# Sentinels shown in the clean pane for rows that never reached the warehouse.
DROPPED = "(dropped)"
QUARANTINED = "(quarantined)"

# ── Cell status vocabulary ───────────────────────────────────────────────────
# "clean"     unchanged, no finding
# "fixed"     the pipeline transformed this value (parsed, coerced, imputed)
# "error"     this cell is WHY the row was quarantined or dropped
# "preserved" a finding the pipeline deliberately kept as-is (TX-03, TX-10, PR-02)
#
# WHY "preserved" is its own status and not "error": a return's negative
# quantity and a silent discount's total are both correct data that the pipeline
# chose not to touch. Painting them red would tell a reviewer the exact opposite
# of the decision being demonstrated, which is how the previous file went wrong.
CLEAN, FIXED, ERROR, PRESERVED = "clean", "fixed", "error", "preserved"


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}\nRun the pipeline first.")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _catalog(output_dir: Path) -> dict[str, dict]:
    """Defect code -> spec. The single source for titles and decisions."""
    path = output_dir / "defect_catalog.json"
    if not path.exists():
        raise SystemExit(f"missing artifact: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("defects", data) if isinstance(data, dict) else data
    if isinstance(entries, dict):
        entries = list(entries.values())
    return {e["code"]: e for e in entries}


def _explain(catalog: dict[str, dict], code: str, extra: str = "") -> str:
    """Prose for a flagged cell, taken from the catalog rather than invented."""
    spec = catalog.get(code)
    if not spec:
        return extra
    text = f"{spec['title']}. {spec.get('decision', '')}".strip()
    return f"{text} {extra}".strip()


def _cell(raw: str, clean: str, status: str, code: str | None, why: str) -> dict:
    return {
        "raw_value": raw,
        "clean_value": clean,
        "status": status,
        "defect_code": code,
        "explanation": why or None,
    }


# ── Transactions ─────────────────────────────────────────────────────────────
def build_transactions(raw_dir: Path, output_dir: Path, catalog: dict) -> dict:
    """One diff row per SOURCE row, in source order.

    Disposition is read from `transactions__lineage.csv`, which the cleaning
    layer writes with one entry per input row. That file is the authority: if
    this function ever disagrees with it, this function is wrong.
    """
    headers, raw_rows = _read_csv(raw_dir / "transactions.csv")
    _, clean_rows = _read_csv(output_dir / "cleaned" / "transactions_clean.csv")
    clean_by_id = {r["transaction_id"]: r for r in clean_rows}

    lineage_path = output_dir / "quarantine" / "transactions__lineage.csv"
    _, lineage_rows = _read_csv(lineage_path)
    lineage = {int(r["source_row"]): r for r in lineage_rows}
    if len(lineage) != len(raw_rows):
        raise SystemExit(
            f"lineage covers {len(lineage)} rows but the raw file has {len(raw_rows)}; "
            "the ledger and the source have diverged."
        )

    rows: list[dict] = []
    for index, raw in enumerate(raw_rows):
        entry = lineage.get(index)
        if entry is None:
            raise SystemExit(f"no lineage entry for source row {index}")
        disposition = entry["disposition"]
        reason = (entry.get("reason_code") or "").strip() or None
        clean = clean_by_id.get(raw["transaction_id"]) if disposition == "kept" else None

        # A row that never loaded has no cleaned counterpart; the sentinel says
        # which of the two exclusion routes it took, and the reason code says why.
        sentinel = DROPPED if disposition == "dropped" else QUARANTINED

        cells: dict[str, dict] = {}
        codes: set[str] = set()

        for col in headers:
            raw_value = raw.get(col, "")
            if clean is None:
                # Excluded row. Only the column responsible carries the code.
                blame = _blame_column(reason)
                if col == blame and reason:
                    codes.add(reason)
                    cells[col] = _cell(
                        raw_value, sentinel, ERROR, reason,
                        _explain(catalog, reason,
                                 f"This row was {disposition} and is not in fact_sales."),
                    )
                else:
                    cells[col] = _cell(raw_value, sentinel, CLEAN, None, "")
                continue

            clean_value = clean.get(col, "")
            status, code, why = CLEAN, None, ""

            if col == "transaction_date" and not _is_iso(raw_value):
                status, code = FIXED, "TX-01"
                why = _explain(catalog, code, f"Parsed from {raw_value!r} to ISO.")
            elif col == "total_amount" and "$" in raw_value:
                status, code = FIXED, "TX-02"
                why = _explain(catalog, code, "Currency string coerced to a number.")
            elif col == "customer_id" and not raw_value.strip():
                status, code = FIXED, "TX-06"
                why = _explain(catalog, code, "Guest checkout; the row is kept.")

            # Findings the pipeline deliberately did NOT change.
            if col == "total_amount" and clean.get("has_discount", "").strip().lower() in ("true", "1"):
                status, code = PRESERVED, "TX-03"
                why = _explain(
                    catalog, code,
                    f"Reported total kept verbatim; discount of "
                    f"{clean.get('discount_amount', '?')} exposed separately.",
                )
            if col == "quantity" and clean.get("is_return", "").strip().lower() in ("true", "1"):
                status, code = PRESERVED, "TX-10"
                why = _explain(
                    catalog, code,
                    "Negative quantity is correct for a return. The row is KEPT and "
                    "reduces net revenue; it is not an error and was not dropped.",
                )

            if code:
                codes.add(code)
            cells[col] = _cell(raw_value, clean_value, status, code, why)

        rows.append({
            "row_id": raw["transaction_id"],
            "disposition": disposition,
            "defects": sorted(codes),
            "cells": cells,
        })

    return {"headers": headers, "rows": rows}


def _blame_column(reason: str | None) -> str | None:
    """Which column explains an excluded row. Mirrors the cleaning rules."""
    return {
        "TX-04": "store_id",
        "TX-05": "product_id",
        "TX-07": "quantity",
        "TX-08": "transaction_date",
        "TX-09": "transaction_id",
    }.get(reason or "")


def _is_iso(value: str) -> bool:
    v = value.strip()
    return len(v) >= 10 and v[4] == "-" and v[7] == "-"


# ── Dimensions ───────────────────────────────────────────────────────────────
def build_dimension(
    name: str, key: str, raw_dir: Path, output_dir: Path, catalog: dict,
    rules: list[tuple[str, str, Any]],
) -> dict:
    """Diff for a dimension table.

    Dimensions have no lineage ledger, so disposition is derived the only other
    honest way: a row whose key is absent from the cleaned output did not
    survive. That is a fact about the artifacts, not a re-implementation of the
    cleaning rules.
    """
    headers, raw_rows = _read_csv(raw_dir / f"{name}.csv")
    _, clean_rows = _read_csv(output_dir / "cleaned" / f"{name}_clean.csv")
    clean_by_key = {r[key]: r for r in clean_rows}

    seen: set[str] = set()
    rows: list[dict] = []
    for raw in raw_rows:
        key_value = raw[key]
        duplicate = key_value in seen
        seen.add(key_value)
        clean = clean_by_key.get(key_value)
        # The second occurrence of a key is the one that lost survivorship.
        survived = clean is not None and not duplicate

        cells: dict[str, dict] = {}
        codes: set[str] = set()
        for col in headers:
            raw_value = raw.get(col, "")
            if not survived:
                code = "ST-02" if name == "stores" else "PR-01"
                if col == key:
                    codes.add(code)
                    cells[col] = _cell(
                        raw_value, DROPPED, ERROR, code,
                        _explain(catalog, code, "This variant lost; the elected row is above."),
                    )
                else:
                    cells[col] = _cell(raw_value, DROPPED, CLEAN, None, "")
                continue

            clean_value = clean.get(col, raw_value) if clean else raw_value
            status, code, why = CLEAN, None, ""
            for rule_col, rule_code, predicate in rules:
                if col == rule_col and predicate(raw, clean):
                    status, code = FIXED, rule_code
                    why = _explain(catalog, rule_code)
                    break
            if code:
                codes.add(code)
            cells[col] = _cell(raw_value, clean_value, status, code, why)

        rows.append({
            "row_id": key_value,
            "disposition": "kept" if survived else "dropped",
            "defects": sorted(codes),
            "cells": cells,
        })

    return {"headers": headers, "rows": rows}


def render(raw_dir: Path, output_dir: Path) -> dict:
    catalog = _catalog(output_dir)
    return {
        "transactions": build_transactions(raw_dir, output_dir, catalog),
        "stores": build_dimension(
            "stores", "store_id", raw_dir, output_dir, catalog,
            [
                ("zip_code", "ST-01", lambda r, c: len(r.get("zip_code", "").strip()) != 5),
                ("region", "ST-03", lambda r, c: not r.get("region", "").strip()),
            ],
        ),
        "products": build_dimension(
            "products", "product_id", raw_dir, output_dir, catalog,
            [
                ("category", "PR-03", lambda r, c: not r.get("category", "").strip()),
                ("unit_price", "PR-04", lambda r, c: float(r.get("unit_price") or 0) == 0),
            ],
        ),
    }


def verify(diff: dict, output_dir: Path) -> list[str]:
    """Assert every transaction row's disposition matches the lineage ledger.

    This is the check whose absence allowed 101 rows to go wrong unnoticed.
    """
    problems: list[str] = []
    _, lineage_rows = _read_csv(output_dir / "quarantine" / "transactions__lineage.csv")
    lineage = {int(r["source_row"]): r for r in lineage_rows}
    rows = diff["transactions"]["rows"]
    if len(rows) != len(lineage):
        problems.append(f"transactions: {len(rows)} diff rows vs {len(lineage)} lineage rows")
    for index, row in enumerate(rows):
        entry = lineage.get(index)
        if not entry:
            problems.append(f"row {index}: no lineage entry")
            continue
        if row["disposition"] != entry["disposition"]:
            problems.append(
                f"row {index} ({row['row_id']}): diff says {row['disposition']!r}, "
                f"lineage says {entry['disposition']!r}"
            )
        if row["row_id"] != entry["transaction_id"]:
            problems.append(f"row {index}: id {row['row_id']} != lineage {entry['transaction_id']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    diff = render(args.raw_dir, args.output_dir)

    problems = verify(diff, args.output_dir)
    if problems:
        print("FAIL  csv_diff disagrees with the pipeline lineage:", file=sys.stderr)
        for p in problems[:20]:
            print(f"  {p}", file=sys.stderr)
        return 1

    serialized = json.dumps(diff, indent=1, ensure_ascii=False)

    if args.check:
        if not args.out.exists():
            print(f"FAIL  {args.out} does not exist", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != serialized:
            print(f"FAIL  {args.out} is stale — regenerate it", file=sys.stderr)
            return 1
        print(f"PASS  {args.out} matches the pipeline artifacts")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialized, encoding="utf-8")
    counts = {k: len(v["rows"]) for k, v in diff.items()}
    print(f"wrote {args.out}  rows={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
