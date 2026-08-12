#!/usr/bin/env python3
"""Prove this submission from a clean clone, in one command, with no arguments.

    python scripts/verify_submission.py

WHAT IT DOES
------------
1. **Preflight** -- interpreter version, pandas, pytest, and that the submission tree is
   where it is supposed to be. Any missing dependency is reported with the exact
   command that fixes it.
2. **Runs the full pipeline** into a fresh temporary directory.
3. **Runs the test suite** (``solution/tests``).
4. **Runs the documentation gate** (``solution/scripts/check_readme_numbers.py``)
   against the artifacts it just produced -- for *both* READMEs, and then checks that
   the two documents cite the same set of figures, so neither can drift from the other.
5. **Re-asserts every headline number independently** from the raw CSVs, the warehouse,
   the audit ledger, the analytics JSON and the lineage file -- deliberately *not*
   through the same code path the pipeline used to produce them.

It prints one pass/fail table and exits non-zero if anything is wrong.

WHY THE INDEPENDENT ASSERTIONS EXIST AT ALL, GIVEN STEPS 2-4
------------------------------------------------------------
The pipeline already fails on its own coverage proof, the suite already pins the
golden numbers, and the doc gate already ties the README to the artifacts. Each of
those, though, is the submission grading its own homework: they share code, fixtures
and assumptions with the thing under test. Step 5 re-derives the same claims from the
serialised artifacts and the raw CSVs using nothing but the standard library, so a
defect in the shared machinery cannot silently satisfy both sides of the comparison.
Where a figure is reachable by two routes -- net revenue from ``fact_sales`` and from
``analytics.json``; the row budget from the lineage file and from ``transactions.csv``
-- both routes are taken and compared.

WHY A TEMPORARY OUTPUT DIRECTORY
--------------------------------
The pipeline is run with ``--output-dir`` pointing at ``tempfile.mkdtemp()``. A
verification run is a read-only operation on the repository: it must not overwrite
whatever is sitting in ``solution/output/``, and two reviewers running it at once must
not collide. The artifacts are kept on disk when a check fails, so there is something
to inspect, and removed on success unless ``--keep-artifacts`` is given.

Exit codes:
    0  every check passed
    1  at least one check failed (the artifacts are left on disk for inspection)
    2  the verification could not run at all -- missing dependency or missing tree

Defects handled: none directly. This is a submission-level gate; the 17 seeded defect
classes are handled in ``solution/src/cleaning/`` and proved here.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# WHY resolve() and parents[1]: the script must work when invoked as
# ``python scripts/verify_submission.py`` from the repo root, as
# ``python ./scripts/verify_submission.py``, or by absolute path from anywhere --
# and on Windows, where the invocation is usually ``python scripts\verify_submission.py``.
# Deriving both roots from __file__ rather than from the current directory makes all
# of those identical.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]
SOLUTION_DIR: Path = REPO_ROOT / "solution"
RAW_DIR: Path = SOLUTION_DIR / "data" / "raw"
ROOT_README: Path = REPO_ROOT / "README.md"
SOLUTION_README: Path = SOLUTION_DIR / "README.md"
DOC_GATE: Path = SOLUTION_DIR / "scripts" / "check_readme_numbers.py"

# WHY pure ASCII in all output: this is the last thing a hiring manager runs, quite
# possibly in a Windows console still defaulting to cp1252, where a box-drawing
# character raises UnicodeEncodeError and the verification "fails" for a reason that
# has nothing to do with the submission. Nothing here is worth that risk.
RULE = "-" * 92
HEAVY = "=" * 92

# Currency is serialised as an IEEE-754 double and published to two decimals. Half a
# cent is tighter than any figure this pipeline publishes and looser than float noise.
MONEY_TOLERANCE = 0.005


# ══════════════════════════════════════════════════════════════════════════════
#  Result model
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Check:
    """One independently verified claim.

    Attributes:
        name: What is being asserted, in the reviewer's language, not the code's.
        expected: The claim, rendered exactly as it should read.
        actual: What the artifacts actually say.
        ok: Whether the two agree.
        detail: Extra context printed only when the check fails.
    """

    name: str
    expected: str
    actual: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    """Accumulated checks plus the stage timings, in the order they were produced."""

    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, expected: Any, actual: Any, *, detail: str = "") -> Check:
        """Record an exact-equality check.

        Args:
            name: Human-readable claim.
            expected: The value the submission claims.
            actual: The value read back from an artifact.
            detail: Context shown only on failure.

        Returns:
            The recorded :class:`Check`.
        """
        chk = Check(name, str(expected), str(actual), str(expected) == str(actual), detail)
        self.checks.append(chk)
        return chk

    def add_money(self, name: str, expected: float, actual: float, *, detail: str = "") -> Check:
        """Record a currency check with a half-cent tolerance.

        Args:
            name: Human-readable claim.
            expected: Published dollar figure.
            actual: Dollar figure recomputed from an artifact.
            detail: Context shown only on failure.

        Returns:
            The recorded :class:`Check`.
        """
        ok = abs(float(expected) - float(actual)) <= MONEY_TOLERANCE
        chk = Check(name, f"${expected:,.2f}", f"${actual:,.2f}", ok, detail)
        self.checks.append(chk)
        return chk

    @property
    def failures(self) -> list[Check]:
        """Every check that did not pass, in declaration order."""
        return [c for c in self.checks if not c.ok]


class Fatal(RuntimeError):
    """The verification could not be run at all. Carries an actionable remedy.

    Attributes:
        remedy: The exact command a reviewer should run next. Printed verbatim.
    """

    def __init__(self, message: str, remedy: str = "") -> None:
        super().__init__(message)
        self.remedy = remedy


# ══════════════════════════════════════════════════════════════════════════════
#  Stage 1 -- preflight
# ══════════════════════════════════════════════════════════════════════════════
def preflight() -> dict[str, str]:
    """Check the interpreter, the dependencies and the repository layout.

    Returns:
        Version strings for the banner: ``python``, ``pandas``, ``pytest``.

    Raises:
        Fatal: If anything needed to run the verification is missing. The message names
            the exact command that fixes it -- a verification tool whose failure mode is
            "ImportError: pandas" has wasted the reviewer's time rather than saved it.
    """
    install_hint = f'"{sys.executable}" -m pip install -r "{REPO_ROOT / "requirements.txt"}"'

    if sys.version_info < (3, 10):
        raise Fatal(
            f"Python {sys.version_info.major}.{sys.version_info.minor} is too old; "
            f"this project requires 3.10 or newer (it uses PEP 604 unions and match-free "
            f"3.10 syntax throughout).",
            remedy="Install Python 3.10+ and re-run with that interpreter.",
        )

    # WHY these three paths specifically: they are the ones every later stage assumes.
    # Failing here names the missing thing; failing later would produce a stack trace
    # from inside pytest or sqlite3 that says nothing about a wrong working directory.
    required = {
        "the submission tree": SOLUTION_DIR,
        "the pipeline entry point": SOLUTION_DIR / "src" / "pipeline.py",
        "the raw data": RAW_DIR / "transactions.csv",
        "the documentation gate": DOC_GATE,
        "the canonical README": ROOT_README,
    }
    for label, path in required.items():
        if not path.exists():
            raise Fatal(
                f"cannot find {label} at {path}",
                remedy="Run this from a complete clone: python scripts/verify_submission.py",
            )

    try:
        import pandas  # noqa: PLC0415 -- probed deliberately, not used by this script
    except ImportError as exc:
        raise Fatal(
            f"pandas is not installed for this interpreter ({sys.executable}).",
            remedy=install_hint,
        ) from exc

    probe = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        raise Fatal(
            f"pytest is not installed for this interpreter ({sys.executable}).",
            remedy=install_hint,
        )
    pytest_version = (probe.stdout or probe.stderr).strip().splitlines()[0]

    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pandas": pandas.__version__,
        "pytest": pytest_version.replace("pytest ", ""),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Subprocess helpers
# ══════════════════════════════════════════════════════════════════════════════
def run_step(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one child process and capture everything it said.

    Args:
        argv: Command line. ``sys.executable`` is always argv[0] here, so the child
            runs on the same interpreter that is running this script -- otherwise a
            machine with several Pythons verifies one and imports another.
        cwd: Working directory for the child.

    Returns:
        The completed process, with stdout and stderr decoded as text.
    """
    # WHY errors="replace": a child writing a byte the console encoding cannot express
    # must not turn into a decoding crash in the verifier. The point of this tool is to
    # report, never to add its own failure mode.
    return subprocess.run(
        list(argv), cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )


def tail(text: str, lines: int = 25) -> str:
    """Return the last ``lines`` non-empty lines of captured output, indented.

    Args:
        text: Captured stdout or stderr.
        lines: How many trailing lines to keep.

    Returns:
        An indented block ready to print under a failure heading.
    """
    kept = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "\n".join(f"      | {ln}" for ln in kept)


# ══════════════════════════════════════════════════════════════════════════════
#  Stage 5 -- independent readers  (stdlib only, no pandas, no src/ imports)
# ══════════════════════════════════════════════════════════════════════════════
def count_raw_rows() -> dict[str, int]:
    """Count data rows in each supplied CSV, without pandas and without the pipeline.

    Returns:
        Mapping of dataset name to row count, excluding the header line.

    WHY the standard-library csv module rather than pandas: pandas is the component
    under test. Counting the source with the same library the pipeline uses to read it
    would make an ingestion bug invisible to this check.
    """
    counts: dict[str, int] = {}
    for name in ("stores", "products", "transactions"):
        with (RAW_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
            counts[name] = sum(1 for _ in csv.DictReader(handle))
    return counts


def quarantine_row_count(output_dir: Path) -> tuple[int, int]:
    """Count rows across every quarantine CSV -- both drops and evidence copies.

    Args:
        output_dir: A completed run's artifact directory.

    Returns:
        ``(total_rows, file_count)``.

    WHY the lineage file is excluded: ``transactions__lineage.csv`` is the 505-row
    per-source-row ledger, not a quarantine slice. Counting it would conflate the row
    budget with the quarantine population -- the exact confusion the README's
    ``dropped`` / ``evidence`` distinction exists to prevent.
    """
    total = 0
    files = 0
    for path in sorted((output_dir / "quarantine").glob("*.csv")):
        if path.name.endswith("__lineage.csv"):
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            total += sum(1 for _ in csv.DictReader(handle))
        files += 1
    return total, files


def lineage_summary(output_dir: Path) -> dict[str, int]:
    """Summarise the per-source-row disposition ledger.

    Args:
        output_dir: A completed run's artifact directory.

    Returns:
        Counts keyed by disposition (``kept`` / ``quarantined`` / ``dropped``), by
        ``reason.<CODE>``, and ``total``.
    """
    counts: dict[str, int] = {"kept": 0, "quarantined": 0, "dropped": 0, "total": 0}
    path = output_dir / "quarantine" / "transactions__lineage.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            counts["total"] += 1
            counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
            code = (row.get("reason_code") or "").strip()
            if code:
                counts[f"reason.{code}"] = counts.get(f"reason.{code}", 0) + 1
    return counts


def cleaned_revenue(output_dir: Path) -> float:
    """Sum ``total_amount`` straight out of the cleaned transactions CSV.

    Args:
        output_dir: A completed run's artifact directory.

    Returns:
        Net revenue as the cleaning layer left it, before the warehouse ever saw it.

    WHY this is the "source" side of the tie-out: it is the last artifact produced
    before the load, so comparing it to ``SUM(fact_sales.net_amount)`` proves the load
    moved the money without altering it. A uniform rescaling inside the warehouse --
    the one error the reconciliation deltas cannot see, because it stays internally
    consistent -- shows up here and nowhere else.
    """
    path = output_dir / "cleaned" / "transactions_clean.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return round(sum(float(row["total_amount"]) for row in csv.DictReader(handle)), 2)


def db_scalar(db_path: Path, sql: str) -> Any:
    """Run one scalar query against the warehouse, read-only.

    Args:
        db_path: ``warehouse.db``.
        sql: A query returning exactly one row and one column.

    Returns:
        The scalar value.
    """
    # WHY mode=ro via a file: URI: verification must be incapable of modifying the
    # thing it is verifying, even by accident (SQLite will otherwise create journal
    # files beside the database just by connecting).
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        return conn.execute(sql).fetchone()[0]


def fk_violations(db_path: Path) -> int:
    """Count referential-integrity violations the database itself reports.

    Args:
        db_path: ``warehouse.db``.

    Returns:
        Number of rows returned by ``PRAGMA foreign_key_check``.

    WHY re-run the pragma rather than trust ``audit_report.json``: the audit report is
    written by the same process that loaded the database. Asking SQLite directly is the
    independent second opinion.
    """
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        return len(conn.execute("PRAGMA foreign_key_check").fetchall())


MARKER_RE = re.compile(r"<!--\s*fig:(?P<fid>[A-Za-z0-9_.\-]+)\s*-->")


def cited_figures(readme: Path) -> set[str]:
    """Collect every ``<!-- fig:id -->`` marker id cited in a document.

    Args:
        readme: A README to scan.

    Returns:
        The set of figure ids the document claims a value for.
    """
    return set(MARKER_RE.findall(readme.read_text(encoding="utf-8")))


# ══════════════════════════════════════════════════════════════════════════════
#  Stage 5 -- the assertions
# ══════════════════════════════════════════════════════════════════════════════
def assert_headline_facts(output_dir: Path, report: Report) -> None:
    """Re-derive every headline claim from the artifacts and record the comparisons.

    Args:
        output_dir: A completed run's artifact directory.
        report: Accumulator the checks are appended to.

    Every expected value below is a literal, on purpose. A check that reads its own
    expectation out of the artifact it is checking proves only that JSON round-trips.
    """
    audit = json.loads((output_dir / "audit_report.json").read_text(encoding="utf-8"))
    analytics = json.loads((output_dir / "analytics.json").read_text(encoding="utf-8"))
    catalog = json.loads((output_dir / "defect_catalog.json").read_text(encoding="utf-8"))
    db = output_dir / "warehouse.db"

    counts = audit["pipeline"]["row_counts"]
    totals = audit["totals"]
    recon = analytics["metrics"]["revenue_reconciliation"]["rows"][0]

    # ── Source completeness ───────────────────────────────────────────────────
    raw = count_raw_rows()
    report.add("raw records read from data/raw (all three CSVs)", 553, sum(raw.values()),
               detail=f"per file: {raw}")
    report.add("raw stores.csv rows", 16, raw["stores"])
    report.add("raw products.csv rows", 32, raw["products"])
    report.add("raw transactions.csv rows", 505, raw["transactions"])

    # ── Defect coverage: 17 classes, counts matching the catalog ──────────────
    specs = {d["code"]: d for d in catalog["defects"]}
    detected = {r["code"]: r["detected_count"] for r in audit["records"]}
    report.add("defect classes in the catalog", 17, len(specs))
    report.add("defect classes detected by the run", 17, totals["defect_classes_detected"])
    report.add("defect classes never recorded", 0, totals["defect_classes_never_recorded"])

    # WHY compare the ledger against the catalog here, rather than reading the
    # pipeline's own mismatch_count: mismatch_count is computed by the code being
    # verified. This recomputes it from the two serialised sides.
    mismatched = [
        f"{code} expected {spec['expected_count']} detected {detected.get(code)}"
        for code, spec in specs.items()
        if spec["expected_count"] is not None and detected.get(code) != spec["expected_count"]
    ]
    report.add("per-defect counts matching the catalog", "17/17 match",
               f"{17 - len(mismatched)}/17 match", detail="; ".join(mismatched))
    report.add("count mismatches reported by the run", 0, totals["mismatch_count"])

    # ── Dimensions and fact ───────────────────────────────────────────────────
    report.add("dim_store rows (15 surviving stores)", 15, db_scalar(db, "SELECT COUNT(*) FROM dim_store"))
    report.add("dim_product rows (30 distinct products)", 30,
               db_scalar(db, "SELECT COUNT(*) FROM dim_product"))
    report.add("fact_sales rows (one per kept source transaction)", 474,
               db_scalar(db, "SELECT COUNT(*) FROM fact_sales"))
    report.add("dim_store rows agree with the audit ledger",
               db_scalar(db, "SELECT COUNT(*) FROM dim_store"), counts["warehouse"]["dim_store"])
    report.add("fact_sales rows agree with the audit ledger",
               db_scalar(db, "SELECT COUNT(*) FROM fact_sales"), counts["warehouse"]["fact_sales"])
    report.add("distinct regions in dim_store (no invented fifth)", 4,
               db_scalar(db, "SELECT COUNT(DISTINCT region) FROM dim_store"))

    # ── Referential integrity ─────────────────────────────────────────────────
    report.add("PRAGMA foreign_key_check violations", 0, fk_violations(db))
    report.add("foreign-key violations reported by the loader", 0,
               counts["warehouse"]["fk_violations"])

    # ── Quarantine: drops and evidence ────────────────────────────────────────
    quarantined, quarantine_files = quarantine_row_count(output_dir)
    report.add("quarantine rows on disk (drops + evidence)", 38, quarantined,
               detail=f"across {quarantine_files} CSV files")
    report.add("quarantine rows agree with the audit ledger", 38, totals["rows_quarantined"])

    # ── The 505-row transaction budget ────────────────────────────────────────
    lineage = lineage_summary(output_dir)
    budget = lineage["kept"] + lineage["quarantined"] + lineage["dropped"]
    report.add("lineage rows kept", 474, lineage["kept"])
    report.add("lineage rows quarantined", 16, lineage["quarantined"])
    report.add("lineage rows dropped", 15, lineage["dropped"])
    report.add("row budget 474 + 16 + 15 reconciles to 505", 505, budget)
    report.add("lineage ledger covers every source row", raw["transactions"], lineage["total"])
    for code, expected in (("TX-04", 5), ("TX-05", 3), ("TX-07", 5), ("TX-08", 3), ("TX-09", 15)):
        report.add(f"lineage rows attributed to {code}", expected, lineage.get(f"reason.{code}", 0))

    # ── Revenue, reached by three independent routes ──────────────────────────
    fact_revenue = round(float(db_scalar(db, "SELECT SUM(net_amount) FROM fact_sales")), 2)
    report.add_money("net revenue in fact_sales", 158044.29, fact_revenue)
    report.add_money("net revenue published in analytics.json", 158044.29,
                     float(recon["net_revenue"]))
    report.add_money("net revenue ties to the cleaned source CSV", cleaned_revenue(output_dir),
                     fact_revenue,
                     detail="SUM(fact_sales.net_amount) vs SUM(transactions_clean.total_amount)")
    report.add_money("revenue tie-out delta (warehouse vs source)", 0.00,
                     abs(cleaned_revenue(output_dir) - fact_revenue))
    report.add("revenue tie-out drift reported by the loader (cents)", 0,
               counts["warehouse"]["revenue_tie_out_cents"])
    report.add_money("reconciliation line-level delta", 0.00, float(recon["line_level_delta"]))
    report.add_money("reconciliation aggregate delta", 0.00, float(recon["aggregate_delta"]))

    # WHY re-add the reconciliation by hand: the published table is an arithmetic
    # chain, and a reader is invited to check it with a calculator. This does exactly
    # that, so the chain cannot quietly stop adding up.
    rebuilt = round(
        float(recon["gross_list_value"]) - float(recon["discount_total"])
        + float(recon["returns_value"]), 2
    )
    report.add_money("gross list - discounts + returns = net revenue", 158044.29, rebuilt,
                     detail="recomputed from the published reconciliation lines")
    report.add_money("TX-03 discounts preserved, not recomputed away", 961.48,
                     float(recon["discount_total"]),
                     detail="a discount_total of $0.00 means total_amount was recomputed")


def assert_root_tree_is_neutralised(report: Report) -> None:
    """Confirm the superseded root ``src/`` and ``tests/`` cannot be mistaken for the code.

    Args:
        report: Accumulator the checks are appended to.

    WHY this is verified rather than merely documented: the failure being defended
    against is a reviewer running the wrong tree and reading the wrong answers. A note
    in a README does not stop that; a module that raises does, and a check that proves
    it still raises stops the shim from being quietly reverted.
    """
    probe = run_step([sys.executable, "-c", "import src"], cwd=REPO_ROOT)
    raised = probe.returncode != 0 and "SUPERSEDED" in probe.stderr
    report.add("root src/ raises on import (superseded first attempt)", "raises",
               "raises" if raised else "imports cleanly",
               detail=tail(probe.stderr, 6))

    collect = run_step(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-o", "addopts="],
        cwd=REPO_ROOT,
    )
    # pytest exits 5 for "no tests collected", which is precisely the intended state.
    ignored = collect.returncode == 5 or "no tests ran" in collect.stdout
    report.add("root tests/ collects nothing (superseded first attempt)", "0 collected",
               "0 collected" if ignored else "collected tests", detail=tail(collect.stdout, 6))


# ══════════════════════════════════════════════════════════════════════════════
#  Rendering
# ══════════════════════════════════════════════════════════════════════════════
def print_banner(versions: dict[str, str], output_dir: Path) -> None:
    """Print the run header.

    Args:
        versions: Output of :func:`preflight`.
        output_dir: Where this run's artifacts are being written.
    """
    print()
    print(HEAVY)
    print("  MINDEX DATA ENGINEERING CHALLENGE  -  SUBMISSION VERIFICATION")
    print(HEAVY)
    print(f"  repository   {REPO_ROOT}")
    print(f"  submission   {SOLUTION_DIR}")
    print(f"  artifacts    {output_dir}")
    print(f"  interpreter  Python {versions['python']}  |  pandas {versions['pandas']}"
          f"  |  pytest {versions['pytest']}")
    print()


def print_stage(index: int, total: int, label: str, status: str, elapsed: float | None = None,
                note: str = "") -> None:
    """Print one stage line, dot-leadered so the statuses align.

    Args:
        index: 1-based stage number.
        total: Number of stages.
        label: What the stage did.
        status: ``ok`` or ``FAILED``.
        elapsed: Seconds the stage took, if measured.
        note: Short extra fact, e.g. ``87 passed``.
    """
    timing = f"{elapsed:6.1f}s" if elapsed is not None else " " * 7
    left = f"  [{index}/{total}] {label} "
    print(f"{left}{'.' * max(4, 66 - len(left))} {status:<8}{timing}  {note}")


def print_table(report: Report) -> None:
    """Print the pass/fail table, then the detail for any failure.

    Args:
        report: The accumulated checks.
    """
    print()
    print(RULE)
    print(f"  {'':<6}{'#':<4}{'CHECK':<46}{'EXPECTED':<18}{'ACTUAL':<18}")
    print(RULE)
    for i, chk in enumerate(report.checks, start=1):
        status = "PASS" if chk.ok else "FAIL"
        name = chk.name if len(chk.name) <= 44 else chk.name[:41] + "..."
        print(f"  {status:<6}{i:<4}{name:<46}{chk.expected:<18}{chk.actual:<18}")
    print(RULE)

    if report.failures:
        print()
        print("  FAILURE DETAIL")
        for chk in report.failures:
            print(f"    - {chk.name}: expected {chk.expected}, got {chk.actual}")
            if chk.detail:
                for line in chk.detail.splitlines():
                    print(f"        {line}")


# ══════════════════════════════════════════════════════════════════════════════
#  Driver
# ══════════════════════════════════════════════════════════════════════════════
def verify(keep_artifacts: bool) -> int:
    """Run every stage and print the result.

    Args:
        keep_artifacts: Leave the temporary output directory in place even on success.

    Returns:
        Process exit code: 0 all checks passed, 1 at least one failed.

    Raises:
        Fatal: If the verification could not be started at all.
    """
    versions = preflight()

    # WHY mkdtemp rather than TemporaryDirectory: on failure the directory must
    # survive so the reviewer can open the artifacts that disagreed. Cleanup is
    # therefore an explicit decision at the end, not a context manager's default.
    output_dir = Path(tempfile.mkdtemp(prefix="mindex-verify-"))
    print_banner(versions, output_dir)

    report = Report()
    stages = 5
    hard_failure = False

    # ── Stage 1 ───────────────────────────────────────────────────────────────
    print_stage(1, stages, "preflight (interpreter, dependencies, tree layout)", "ok")

    # ── Stage 2: the pipeline ─────────────────────────────────────────────────
    started = time.monotonic()
    pipeline = run_step(
        [sys.executable, "-m", "src.pipeline", "--output-dir", str(output_dir)],
        cwd=SOLUTION_DIR,
    )
    elapsed = time.monotonic() - started
    ok = pipeline.returncode == 0
    print_stage(2, stages, "pipeline: raw CSV -> cleaned -> warehouse -> analytics",
                "ok" if ok else "FAILED", elapsed,
                "exit 0, 17/17 defect classes" if ok else f"exit {pipeline.returncode}")
    report.add("pipeline exit code (0 = 17/17 defect classes detected)", 0, pipeline.returncode,
               detail=tail(pipeline.stdout + "\n" + pipeline.stderr))
    if not ok:
        # WHY stop here: every later stage reads artifacts this stage was supposed to
        # produce. Continuing would bury one real failure under twenty derived ones.
        hard_failure = True

    # ── Stage 3: the test suite ───────────────────────────────────────────────
    if not hard_failure:
        started = time.monotonic()
        # WHY -o addopts=: solution/pyproject.toml sets "-q", and a second -q suppresses
        # the summary line the pass count is parsed from.
        tests = run_step(
            [sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "--no-header"],
            cwd=SOLUTION_DIR,
        )
        elapsed = time.monotonic() - started
        passed = re.search(r"(\d+) passed", tests.stdout)
        count = int(passed.group(1)) if passed else 0
        ok = tests.returncode == 0
        print_stage(3, stages, "test suite (solution/tests)", "ok" if ok else "FAILED",
                    elapsed, f"{count} passed" if ok else f"exit {tests.returncode}")
        report.add("pytest exit code", 0, tests.returncode, detail=tail(tests.stdout))
        report.add("tests passed", 87, count, detail=tail(tests.stdout, 8))

    # ── Stage 4: the documentation gate, on both READMEs ──────────────────────
    if not hard_failure:
        started = time.monotonic()
        gate_ok = True
        for label, readme in (("solution/README.md", SOLUTION_README),
                              ("README.md", ROOT_README)):
            gate = run_step(
                [sys.executable, str(DOC_GATE), "--output-dir", str(output_dir),
                 "--readme", str(readme)],
                cwd=SOLUTION_DIR,
            )
            gate_ok &= gate.returncode == 0
            report.add(f"every published figure in {label} matches this run", 0,
                       gate.returncode, detail=tail(gate.stdout, 20))

        # WHY figure parity and not a textual diff: the two documents legitimately
        # differ in their paths and their opening orientation. What must never differ
        # is the set of numbers they publish -- that is the drift that matters, and it
        # is what this check asserts.
        summary_gate = run_step(
            [sys.executable, str(SOLUTION_DIR / "scripts" / "generate_executive_summary.py"),
             "--output-dir", str(output_dir), "--check"],
            cwd=SOLUTION_DIR,
        )
        gate_ok &= summary_gate.returncode == 0
        report.add("executive summary matches this run", 0,
                   summary_gate.returncode, detail=tail(summary_gate.stdout + "\n" + summary_gate.stderr, 20))
        root_figs, solution_figs = cited_figures(ROOT_README), cited_figures(SOLUTION_README)
        only_root = sorted(root_figs - solution_figs)
        only_solution = sorted(solution_figs - root_figs)
        report.add("both READMEs cite the same set of figures", "identical",
                   "identical" if not (only_root or only_solution) else "diverged",
                   detail=f"only in README.md: {only_root}\n"
                          f"only in solution/README.md: {only_solution}")
        elapsed = time.monotonic() - started
        print_stage(4, stages, "documentation gate (both READMEs vs this run)",
                    "ok" if gate_ok else "FAILED", elapsed,
                    f"{len(root_figs)} figures, both documents")

    # ── Stage 5: independent assertions ───────────────────────────────────────
    if not hard_failure:
        started = time.monotonic()
        before = len(report.checks)
        assert_headline_facts(output_dir, report)
        assert_root_tree_is_neutralised(report)
        elapsed = time.monotonic() - started
        added = len(report.checks) - before
        stage_ok = all(c.ok for c in report.checks[before:])
        print_stage(5, stages, "independent assertions (stdlib re-derivation)",
                    "ok" if stage_ok else "FAILED", elapsed, f"{added} claims re-derived")

    print_table(report)

    passed_count = len(report.checks) - len(report.failures)
    print()
    if report.failures:
        print(f"  RESULT   FAIL  -  {len(report.failures)} of {len(report.checks)} checks failed.")
        print(f"  ARTIFACTS kept for inspection at {output_dir}")
        print()
        return 1

    print(f"  RESULT   PASS  -  {passed_count} of {len(report.checks)} checks passed.")
    print("           The pipeline runs, the suite is green, both READMEs match the run,")
    print("           and every headline figure was re-derived from the artifacts.")
    if keep_artifacts:
        print(f"  ARTIFACTS {output_dir}")
    else:
        shutil.rmtree(output_dir, ignore_errors=True)
    print()
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list, for tests. ``None`` reads ``sys.argv``.

    Returns:
        Process exit code -- 0 pass, 1 check failure, 2 could not run.
    """
    parser = argparse.ArgumentParser(
        prog="verify_submission.py",
        description="Run the pipeline, the tests, the documentation gate and an "
                    "independent re-derivation of every headline figure.",
        epilog="Exit 0 = verified, 1 = a check failed, 2 = could not run.",
    )
    parser.add_argument("--keep-artifacts", action="store_true",
                        help="Keep the temporary output directory even when everything passes.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        return verify(keep_artifacts=args.keep_artifacts)
    except Fatal as exc:
        print()
        print(HEAVY)
        print("  CANNOT VERIFY")
        print(HEAVY)
        print(f"  {exc}")
        if exc.remedy:
            print()
            print("  Fix it with:")
            print(f"    {exc.remedy}")
        print()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
