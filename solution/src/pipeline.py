"""Orchestrator: raw CSV -> profile -> clean -> warehouse -> analytics -> proof.

Run it::

    python -m src.pipeline
    python -m src.pipeline --as-of 2026-06-02 --output-dir output --skip-dashboard-export

Stage order, and why it is this order and not another:

1. **read**      -- every column as ``str``, so nothing is coerced before it is
                    inspected (see :func:`src.io_utils.read_csv_as_str`).
2. **profile**   -- measure the raw files *before* cleaning. A profile taken
                    after cleaning describes the pipeline, not the data, and is
                    worthless as evidence that a defect was ever there.
3. **clean**     -- dimensions first, then transactions. Transactions need the
                    surviving store/product key sets to test referential
                    integrity (TX-04, TX-05), so this ordering is a hard
                    dependency, not a preference.
4. **load**      -- dims before fact, whole load in one transaction, foreign
                    keys ON. A partial warehouse is worse than none.
5. **analyze**   -- reads the warehouse back through SQL, so the numbers on the
                    dashboard are the numbers in the database.
6. **report**    -- quarantine CSVs, ``audit_report.json``, defect catalog,
                    ``dashboard_bundle.json``.
7. **prove**     -- ``assert_all_expected_defects_found()``. If the pipeline did
                    not find everything ``scripts/seed_data.py`` provably
                    injected, the run **fails** with exit code 1. A clean-looking
                    run that silently skipped a check is the exact failure mode
                    this whole submission argues against, so it is made
                    impossible here rather than merely discouraged.

Defect codes owned: none directly -- the cleaning modules own all 17. This
module owns the guarantee that every one of them is checked and reported.

Exit codes:
    0 -- pipeline completed and all 17 defect classes were found as expected.
    1 -- pipeline completed but the coverage proof failed (mismatches printed).
    2 -- pipeline raised; nothing downstream should trust the outputs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import inspect
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src import __version__
from src.audit import AuditLog
from src.config import (
    AS_OF_DATE,
    OUTPUT_DIR,
    PRICE_TOLERANCE,
    RAW_DIR,
    RECENT_WINDOW_DAYS,
    RETURN_RATE_ALERT_THRESHOLD,
    SRC_DIR,
    RunConfig,
)
from src.defects import CATALOG_VERSION, DEFECT_CATALOG, DefectCode, catalog_to_dict
from src.io_utils import (
    ensure_output_dirs,
    read_raw_datasets,
    scan_defect_tags,
    write_dataframe_csv,
    write_json_atomic,
)

# ── Downstream stages, built in parallel by other agents ──────────────────────
# WHY module-level imports for cleaning and profiling but late resolution for
# warehouse and analytics: the build contract fixes the cleaning and profiling
# signatures exactly, so importing the symbols directly is honest and gives the
# clearest possible ImportError if one is missing. The warehouse loader and the
# analytics runner have contract-defined *outputs* but no contract-defined
# function name, so those two are resolved by a documented alias list below
# (see :func:`_resolve_callable`) rather than by guessing one spelling and
# failing the whole run on a naming difference.
from src.cleaning.products import clean_products
from src.cleaning.stores import clean_stores
from src.cleaning.transactions import clean_transactions
from src.profiling.profiler import profile

from src.analytics import runner as analytics_runner
from src.warehouse import loader as warehouse_loader

DATASET_ORDER: tuple[str, ...] = ("stores", "products", "transactions")
"""Fixed iteration order. WHY: profile reports, console output and the dashboard
bundle should be diffable between runs, and dict ordering from a glob is not a
guarantee worth relying on."""


# ── Small console helpers ─────────────────────────────────────────────────────
def _banner(text: str) -> None:
    """Print a section banner so a long run is readable in a terminal."""
    print(f"\n{'-' * 78}\n  {text}\n{'-' * 78}", flush=True)


def _line(text: str = "") -> None:
    """Print an indented detail line."""
    print(f"  {text}", flush=True)


# ── Cross-agent call adapters ─────────────────────────────────────────────────
def _resolve_callable(module: Any, *candidate_names: str) -> Callable[..., Any]:
    """Find the first callable on ``module`` matching one of ``candidate_names``.

    WHY this exists: this repository is built by several agents working in
    parallel against a contract that specifies the warehouse and analytics
    *outputs* (``output/warehouse.db``, ``output/analytics.json``) but not the
    name of the function that produces them. Rather than hard-coding one guess
    and failing the entire run on a naming difference, the orchestrator accepts
    a small, documented set of spellings and -- crucially -- raises an error
    that names the module, the names it tried, and what it actually found. A
    reviewer reading a failure gets the fix in the message.

    Args:
        module: Imported module to search.
        *candidate_names: Accepted function names, in order of preference. The
            first name is the canonical one and should be preferred by the
            module's author.

    Returns:
        The resolved callable.

    Raises:
        AttributeError: If none of the names exist, listing the public callables
            that do.

    Defects handled: none (integration plumbing).
    """
    for name in candidate_names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    available = sorted(
        n for n, v in vars(module).items() if callable(v) and not n.startswith("_")
    )
    raise AttributeError(
        f"{module.__name__} exposes none of {list(candidate_names)}. "
        f"Public callables found: {available or '(none)'}. "
        f"Rename your entry point to '{candidate_names[0]}' or tell the foundation agent."
    )


def _call_with_supported_kwargs(fn: Callable[..., Any], **kwargs: Any) -> Any:
    """Call ``fn`` passing only the keyword arguments it actually declares.

    WHY: the orchestrator has more context than any single stage needs (config,
    audit log, dataframes, paths). Filtering by the callee's own signature means
    a stage that does not want the audit log is not forced to accept one, and a
    stage that later grows a parameter starts receiving it with no change here.
    The alternative -- rigid positional calls -- turns every signature tweak in
    a parallel-built module into a crash in this file.

    A required parameter with no default that we cannot supply is a genuine
    contract breach, so it raises immediately with the parameter named.

    Args:
        fn: The target callable.
        **kwargs: Superset of arguments the callee might want.

    Returns:
        Whatever ``fn`` returns.

    Raises:
        TypeError: If ``fn`` requires a parameter this orchestrator cannot
            supply.

    Defects handled: none (integration plumbing).
    """
    sig = inspect.signature(fn)
    accepts_var_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_var_kwargs:
        return fn(**kwargs)

    passable: dict[str, Any] = {}
    missing: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if name in kwargs:
            passable[name] = kwargs[name]
        elif param.default is inspect.Parameter.empty:
            missing.append(name)
    if missing:
        raise TypeError(
            f"{fn.__module__}.{fn.__qualname__} requires parameter(s) {missing} that the "
            f"pipeline cannot supply. Available: {sorted(kwargs)}."
        )
    return fn(**passable)


# ── Run result ────────────────────────────────────────────────────────────────
@dataclass
class PipelineResult:
    """Everything one run produced, returned so tests can assert on it in-process.

    WHY a return value rather than only side effects on disk: the test suite
    should be able to run the whole pipeline and inspect the audit log directly,
    without parsing JSON back off the filesystem and without a subprocess.

    Attributes:
        config: The resolved :class:`~src.config.RunConfig` for this run.
        audit: The populated decision ledger.
        profile_report: Raw-data profile, keyed by dataset.
        analytics: The analytics payload (contract §6 shape).
        row_counts: Rows in / rows out for each stage.
        mismatches: Result of the completeness proof; empty means success.
        duration_seconds: Wall-clock runtime, for the dashboard's run metadata.
    """

    config: RunConfig
    audit: AuditLog
    profile_report: dict[str, Any] = field(default_factory=dict)
    analytics: dict[str, Any] = field(default_factory=dict)
    row_counts: dict[str, Any] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """True when the run found every defect class the catalog expects."""
        return not self.mismatches


# ── Stage 1: read ─────────────────────────────────────────────────────────────
def stage_read(cfg: RunConfig) -> dict[str, pd.DataFrame]:
    """Load the three raw CSVs with every column as a string.

    Args:
        cfg: Resolved run configuration.

    Returns:
        ``{"stores": df, "products": df, "transactions": df}``.

    Defects handled: none directly. The ``dtype=str`` read is what keeps ST-01,
        TX-01, TX-02, TX-07 and PR-04 visible long enough to be detected.
    """
    _banner("STAGE 1/6  read raw CSVs (dtype=str -- no inference, no silent coercion)")
    raw = read_raw_datasets(cfg.raw_dir)
    for name in DATASET_ORDER:
        frame = raw[name]
        _line(f"{name:<14} {len(frame):>4} rows x {len(frame.columns)} cols  <- {name}.csv")
    return raw


# ── Stage 2: profile ──────────────────────────────────────────────────────────
def stage_profile(raw: dict[str, pd.DataFrame], cfg: RunConfig) -> dict[str, Any]:
    """Profile every raw dataset before a single value is changed.

    Args:
        raw: The string-typed raw frames from :func:`stage_read`.
        cfg: Resolved run configuration.

    Returns:
        ``{"generated_at": ..., "as_of_date": ..., "datasets": {name: profile}}``,
        also written to ``output/profile_report.json``.

    Defects handled: none directly -- profiling *observes* all 17 (null counts,
        duplicate counts, format histograms) but changes nothing. Its value is
        evidentiary: it is the "before" photograph that makes every later
        cleaning claim checkable.
    """
    _banner("STAGE 2/6  profile raw data (evidence captured BEFORE any cleaning)")
    profiles: dict[str, Any] = {}
    for name in DATASET_ORDER:
        profiles[name] = profile(raw[name], name)
        _line(f"{name:<14} profiled")

    report = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_date": cfg.as_of_date.isoformat(),
        "datasets": profiles,
    }
    write_json_atomic(cfg.profile_report_path, report)
    _line(f"wrote {cfg.profile_report_path}")
    return report


# ── Stage 3: clean ────────────────────────────────────────────────────────────
def stage_clean(
    raw: dict[str, pd.DataFrame], audit: AuditLog, cfg: RunConfig
) -> dict[str, pd.DataFrame]:
    """Clean dimensions, then transactions, recording every decision in ``audit``.

    Ordering is a hard dependency and not a style choice: ``clean_transactions``
    tests referential integrity against the store and product keys that actually
    *survived* dimension cleaning. Running it first (or against the raw key
    sets) would let a transaction referencing the losing S007 variant, or a
    dropped duplicate product, pass a check it should fail.

    Args:
        raw: String-typed raw frames.
        audit: Ledger mutated in place by each cleaner (contract §4).
        cfg: Resolved run configuration.

    Returns:
        ``{"stores": df, "products": df, "transactions": df}``, cleaned. Also
        snapshotted to ``output/cleaned/*.csv`` so a reviewer can diff before
        and after without running anything.

    Defects handled: all 17, delegated to the cleaning modules.
    """
    _banner("STAGE 3/6  clean (detect -> decide -> record; nothing is dropped silently)")

    stores = clean_stores(raw["stores"], audit)
    _line(f"stores        {len(raw['stores']):>4} -> {len(stores):>4} rows")

    products = clean_products(raw["products"], audit)
    _line(f"products      {len(raw['products']):>4} -> {len(products):>4} rows")

    # WHY the key sets come from the CLEANED dimensions: see the docstring. This
    # is the line that makes TX-04/TX-05 mean "not in the warehouse" rather than
    # the weaker "not in the raw file".
    valid_store_ids: set[str] = set(stores["store_id"].astype(str))
    valid_product_ids: set[str] = set(products["product_id"].astype(str))

    transactions = clean_transactions(
        raw["transactions"], audit, valid_store_ids, valid_product_ids
    )
    _line(f"transactions  {len(raw['transactions']):>4} -> {len(transactions):>4} rows")

    cleaned = {"stores": stores, "products": products, "transactions": transactions}
    ensure_output_dirs(cfg.cleaned_dir)
    for name, frame in cleaned.items():
        write_dataframe_csv(frame, cfg.cleaned_dir / f"{name}_clean.csv")
    _line(f"wrote cleaned snapshots to {cfg.cleaned_dir}")
    return cleaned


# ── Stage 4: load ─────────────────────────────────────────────────────────────
def stage_load(cleaned: dict[str, pd.DataFrame], audit: AuditLog, cfg: RunConfig) -> dict[str, Any]:
    """Build the SQLite star schema at ``output/warehouse.db``.

    Args:
        cleaned: Output of :func:`stage_clean`.
        audit: Ledger, passed through in case the loader records anything it
            rejects at the constraint level.
        cfg: Resolved run configuration.

    Returns:
        Row counts per table, as reported by the loader.

    Defects handled: TX-04/TX-05 indirectly -- ``PRAGMA foreign_keys = ON``
        means an orphan that somehow escaped cleaning cannot be loaded, so the
        database is a second, independent enforcement of referential integrity
        rather than a mirror of the cleaner's opinion.
    """
    _banner("STAGE 4/6  load star schema (dims -> fact, one transaction, FKs enforced)")
    load_fn = _resolve_callable(warehouse_loader, "load_warehouse", "build_warehouse", "load")
    counts = _call_with_supported_kwargs(
        load_fn,
        stores=cleaned["stores"],
        products=cleaned["products"],
        transactions=cleaned["transactions"],
        db_path=cfg.db_path,
        audit=audit,
        as_of_date=cfg.as_of_date,
        config=cfg,
    )
    counts = counts if isinstance(counts, dict) else {"result": counts}
    for table, count in counts.items():
        _line(f"{table:<20} {count}")
    _line(f"wrote {cfg.db_path}")
    return counts


# ── Stage 5: analytics ────────────────────────────────────────────────────────
def stage_analytics(cfg: RunConfig, audit: AuditLog) -> dict[str, Any]:
    """Execute the business metrics against the warehouse and serialise them.

    Args:
        cfg: Resolved run configuration.
        audit: Ledger, available to the runner for any data-quality caveat it
            wants to attach to a metric.

    Returns:
        The analytics payload in the contract §6 shape, also written to
        ``output/analytics.json``.

    Defects handled: TX-03, TX-06 and TX-10 surface here as reported numbers --
        the revenue reconciliation ties gross list value, discounts and returns
        back to net revenue, which is what makes the TX-03 decision auditable
        instead of merely asserted.
    """
    _banner("STAGE 5/6  analytics (SQL against the warehouse, not against DataFrames)")
    run_fn = _resolve_callable(
        analytics_runner, "run_analytics", "run", "execute_analytics", "main"
    )
    payload = _call_with_supported_kwargs(
        run_fn,
        db_path=cfg.db_path,
        as_of_date=cfg.as_of_date,
        output_path=cfg.analytics_path,
        audit=audit,
        config=cfg,
    )
    payload = payload if isinstance(payload, dict) else {"metrics": {}}

    metrics = payload.get("metrics", {})
    for metric_id, metric in metrics.items():
        rows = metric.get("rows", []) if isinstance(metric, dict) else []
        _line(f"{metric_id:<28} {len(rows):>3} rows")

    # WHY write it here even though the runner is also given output_path: the
    # orchestrator owns the artifact contract. If the runner already wrote the
    # file, this rewrites identical content atomically; if it did not, the file
    # still exists. Either way the dashboard never faces a missing analytics.json.
    write_json_atomic(cfg.analytics_path, payload)
    _line(f"wrote {cfg.analytics_path}")
    return payload


# ── Stage 6: report + proof ───────────────────────────────────────────────────
def build_code_index() -> dict[str, list[dict[str, Any]]]:
    """Map every defect code to the source lines tagged ``# DEFECT: <CODE>``.

    Codes with no tag anywhere still appear, with an empty list. WHY: an absent
    key would render on the dashboard as "no data"; an explicit empty list
    renders as "implemented but untagged", which is a real and actionable gap.

    Returns:
        ``{"TX-03": [{"file", "line", "snippet"}, ...], ...}`` for all 17 codes.

    Defects handled: all 17 (indexing).
    """
    found = scan_defect_tags(SRC_DIR)
    return {code.value: found.get(code.value, []) for code in DefectCode}


def stage_report(
    result: PipelineResult, code_index: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """Write quarantine files, the audit report, the catalog, and run the proof.

    Args:
        result: The in-progress run result; ``mismatches`` is populated here.
        code_index: Output of :func:`build_code_index`, embedded in the report.

    Returns:
        The list of coverage mismatches (empty on success).

    Defects handled: all 17 (verification and reporting).
    """
    cfg, audit = result.config, result.audit
    _banner("STAGE 6/6  audit report + defect-coverage proof")

    quarantined = audit.write_quarantine_files(cfg.quarantine_dir)
    for name, path in sorted(quarantined.items()):
        _line(f"quarantined {name:<28} -> {Path(path).name}")
    if not quarantined:
        _line("no rows quarantined")

    # WHY the proof runs before the report is written, and its output is stored
    # inside the report: the audit_report.json a reviewer opens must state
    # whether the run passed, not leave them to re-derive it from the counts.
    mismatches = audit.assert_all_expected_defects_found()
    result.mismatches = mismatches

    report = audit.to_dict()
    report["code_index"] = code_index
    report["pipeline"] = {
        "version": __version__,
        "as_of_date": cfg.as_of_date.isoformat(),
        "raw_dir": str(cfg.raw_dir),
        "output_dir": str(cfg.output_dir),
        "row_counts": result.row_counts,
    }
    write_json_atomic(cfg.audit_report_path, report)
    _line(f"wrote {cfg.audit_report_path}")

    write_json_atomic(cfg.defect_catalog_path, catalog_to_dict())
    _line(f"wrote {cfg.defect_catalog_path}")
    return mismatches


def build_dashboard_bundle(
    result: PipelineResult, code_index: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Merge every artifact into the single file the dashboard consumes.

    WHY one bundle instead of letting the front-end fetch five files: the five
    artifacts are only meaningful together (a defect record means little without
    its spec, and neither means much without the code location), and five
    independent fetches can observe five different pipeline runs. One atomically
    written file is one consistent snapshot, always.

    Args:
        result: The completed run result.
        code_index: Output of :func:`build_code_index`.

    Returns:
        The bundle, also written to ``output/dashboard_bundle.json``.

    Defects handled: all 17 (presentation).
    """
    cfg, audit = result.config, result.audit
    bundle: dict[str, Any] = {
        # ── run metadata ─────────────────────────────────────────────────────
        "run": {
            "pipeline_version": __version__,
            "catalog_version": CATALOG_VERSION,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "as_of_date": cfg.as_of_date.isoformat(),
            "recent_window_days": RECENT_WINDOW_DAYS,
            "recent_window_start": cfg.recent_window_start.isoformat(),
            "price_tolerance": PRICE_TOLERANCE,
            "return_rate_alert_threshold": RETURN_RATE_ALERT_THRESHOLD,
            "duration_seconds": round(result.duration_seconds, 3),
            "raw_dir": str(cfg.raw_dir),
            "output_dir": str(cfg.output_dir),
            "row_counts": result.row_counts,
            "status": "pass" if result.ok else "fail",
            "mismatch_count": len(result.mismatches),
        },
        # ── the four content blocks ──────────────────────────────────────────
        "defect_catalog": catalog_to_dict(),
        "audit": audit.to_dict(),
        "profiling": result.profile_report,
        "analytics": result.analytics,
        # ── code links ───────────────────────────────────────────────────────
        "code_index": code_index,
        # WHY a pre-computed coverage block: every dashboard view needs
        # "17 of 17 found", and computing it in three different components is
        # three chances to compute it three different ways.
        "coverage": {
            "expected_classes": len(DEFECT_CATALOG),
            "detected_classes": sum(
                1 for code in DEFECT_CATALOG if audit.get(code) is not None
            ),
            "matched_classes": sum(
                1
                for code, spec in DEFECT_CATALOG.items()
                if (rec := audit.get(code)) is not None
                and (spec.expected_count is None or rec.detected_count == spec.expected_count)
            ),
            "untagged_codes": [c for c, hits in code_index.items() if not hits],
            "mismatches": result.mismatches,
        },
    }
    write_json_atomic(cfg.dashboard_bundle_path, bundle)
    _line(f"wrote {cfg.dashboard_bundle_path}")
    return bundle


def print_coverage_summary(result: PipelineResult) -> None:
    """Print the reviewer-facing defect-coverage table and the pass/fail verdict.

    Args:
        result: The completed run result.

    Defects handled: all 17 (reporting).
    """
    audit = result.audit
    _banner("DEFECT COVERAGE  (detected vs. what seed_data.py provably injected)")
    print(audit.summary_markdown(["Code", "Dataset", "Severity", "Expected", "Detected", "Status"]))

    matched = sum(1 for row in audit.summary_table() if row["Status"] == "OK")
    total = len(DEFECT_CATALOG)
    print()
    if result.ok:
        _line(f"PASS  {matched}/{total} defect classes detected with counts matching the catalog.")
    else:
        _line(f"FAIL  {matched}/{total} matched. {len(result.mismatches)} problem(s):")
        for mismatch in result.mismatches:
            _line(f"  - {mismatch}")


# ── Orchestration ─────────────────────────────────────────────────────────────
def run_pipeline(cfg: RunConfig) -> PipelineResult:
    """Execute every stage in order and return the collected result.

    Args:
        cfg: Resolved run configuration.

    Returns:
        A :class:`PipelineResult`. ``result.ok`` is the completeness verdict.

    Defects handled: all 17, via the cleaning modules; this function owns the
        guarantee that every one is checked, reported and proved.
    """
    started = time.perf_counter()
    ensure_output_dirs(cfg.output_dir, cfg.cleaned_dir, cfg.quarantine_dir)

    audit = AuditLog(as_of_date=cfg.as_of_date)
    result = PipelineResult(config=cfg, audit=audit)

    raw = stage_read(cfg)
    result.row_counts["raw"] = {name: len(frame) for name, frame in raw.items()}

    result.profile_report = stage_profile(raw, cfg)

    cleaned = stage_clean(raw, audit, cfg)
    result.row_counts["cleaned"] = {name: len(frame) for name, frame in cleaned.items()}
    result.row_counts["quarantined"] = audit.quarantined_row_count

    result.row_counts["warehouse"] = stage_load(cleaned, audit, cfg)
    result.analytics = stage_analytics(cfg, audit)

    code_index = build_code_index()
    stage_report(result, code_index)

    result.duration_seconds = time.perf_counter() - started
    if not cfg.skip_dashboard_export:
        build_dashboard_bundle(result, code_index)
    else:
        _line("dashboard bundle skipped (--skip-dashboard-export)")

    print_coverage_summary(result)
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_as_of(value: str) -> dt.date:
    """Argparse type for ``--as-of``: strict ISO-8601 only.

    Args:
        value: The raw command-line string.

    Returns:
        The parsed date.

    Raises:
        argparse.ArgumentTypeError: On anything that is not ``YYYY-MM-DD``.
            WHY strict: this project's headline finding (TX-01) is that loose
            date parsing silently produces wrong answers. Accepting
            ``06/02/2026`` on the command line would be hypocritical, and would
            reintroduce exactly the US/EU ambiguity the pipeline exists to
            eliminate.

    Defects handled: TX-08 (supplies the reference date it compares against).
    """
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--as-of must be ISO-8601 (YYYY-MM-DD); got {value!r}"
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the command-line interface.

    Returns:
        A configured :class:`argparse.ArgumentParser`.

    Defects handled: none (CLI).
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.pipeline",
        description=(
            "Mindex data-engineering challenge pipeline: profiles, cleans, loads and "
            "analyses the seeded retail dataset, then proves it found all 17 defect classes."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes:\n"
            "  0  all 17 defect classes detected with the expected counts\n"
            "  1  pipeline ran but the defect-coverage proof failed\n"
            "  2  pipeline raised an exception\n"
        ),
    )
    parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        default=AS_OF_DATE,
        metavar="YYYY-MM-DD",
        help=(
            "Reference 'today' for TX-08 and all trailing-window analytics "
            f"(default: {AS_OF_DATE.isoformat()}, which is seed_data.py's own TODAY -- "
            "using wall-clock now would empty the 30-day window and make runs "
            "non-reproducible)."
        ),
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_DIR,
        metavar="DIR",
        help=f"Directory holding the three source CSVs (default: {RAW_DIR}).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        metavar="DIR",
        help=f"Root for all generated artifacts (default: {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--skip-dashboard-export",
        action="store_true",
        help="Do not write output/dashboard_bundle.json (CI only needs the audit report).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``. Injectable so the
            test suite can drive the CLI without a subprocess.

    Returns:
        A process exit code: 0 pass, 1 coverage failure, 2 exception.

    Defects handled: all 17 (orchestration and verification).
    """
    args = build_arg_parser().parse_args(argv)
    cfg = RunConfig(
        as_of_date=args.as_of,
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        skip_dashboard_export=args.skip_dashboard_export,
    )

    _banner(f"MINDEX DATA PIPELINE v{__version__}   as-of {cfg.as_of_date.isoformat()}")
    _line(f"raw    : {cfg.raw_dir}")
    _line(f"output : {cfg.output_dir}")

    try:
        result = run_pipeline(cfg)
    except Exception:  # noqa: BLE001 - top-level boundary; we re-raise as exit code 2
        # WHY print the traceback rather than a friendly message: this is a
        # developer-facing ETL tool, and swallowing the stack trace of a failed
        # load is precisely the behaviour that makes broken pipelines expensive.
        traceback.print_exc()
        _banner("PIPELINE FAILED -- outputs are incomplete and must not be trusted")
        return 2

    # WHY a non-zero exit on mismatches: this makes the completeness proof a
    # build gate. A pipeline that quietly stopped detecting TX-03 would still
    # produce a perfectly plausible dashboard; here it breaks the build instead.
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
