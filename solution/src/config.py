"""Single source of truth for every path, date and threshold used by the pipeline.

Why this module exists
----------------------
Every magic number in a data pipeline is a future bug. Anything that a reviewer
might reasonably want to change -- the reference date, the reconciliation
tolerance, the alerting threshold -- lives here exactly once, is typed, and is
commented with the reason it holds the value it does.

Defect codes owned: none directly, but two constants are load-bearing for
several of them:

* ``AS_OF_DATE``      -- TX-08 (future-dated transactions) and every
                         time-relative analytic depend on it.
* ``PRICE_TOLERANCE`` -- TX-03 (silent discount) is defined as a breach of it.

Inputs:  none (pure constants).
Outputs: module-level constants + :class:`RunConfig` for per-run overrides.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

# ── Filesystem layout ─────────────────────────────────────────────────────────
# WHY: resolve() from __file__ rather than cwd, so the pipeline behaves
# identically whether it is launched from the repo root, from src/, from pytest,
# or from a scheduler with an arbitrary working directory.
SRC_DIR: Path = Path(__file__).resolve().parent
REPO_ROOT: Path = SRC_DIR.parent

RAW_DIR: Path = REPO_ROOT / "data" / "raw"
"""Immutable input. Nothing in the pipeline may ever write here (contract §0)."""

OUTPUT_DIR: Path = REPO_ROOT / "output"
"""All generated artifacts. Gitignored except for .gitkeep."""

CLEANED_DIR: Path = OUTPUT_DIR / "cleaned"
"""Post-cleaning CSV snapshots -- the reviewer's diffable view of what changed."""

QUARANTINE_DIR: Path = OUTPUT_DIR / "quarantine"
"""Rejected rows, one file per (dataset, defect code). Nothing is ever deleted
silently: if the pipeline refuses a row, that row is written to disk here so the
loss is auditable rather than invisible."""

DB_PATH: Path = OUTPUT_DIR / "warehouse.db"
SCHEMA_SQL_PATH: Path = SRC_DIR / "warehouse" / "schema.sql"

# ── Named artifact paths ──────────────────────────────────────────────────────
# WHY: the dashboard, the tests and the docs all need to agree on these names.
# Centralising them means a rename is a one-line change, not a grep-and-pray.
PROFILE_REPORT_PATH: Path = OUTPUT_DIR / "profile_report.json"
AUDIT_REPORT_PATH: Path = OUTPUT_DIR / "audit_report.json"
ANALYTICS_PATH: Path = OUTPUT_DIR / "analytics.json"
DEFECT_CATALOG_JSON_PATH: Path = OUTPUT_DIR / "defect_catalog.json"
DASHBOARD_BUNDLE_PATH: Path = OUTPUT_DIR / "dashboard_bundle.json"

# ── Reference date ────────────────────────────────────────────────────────────
# WHY: scripts/seed_data.py line 22 sets ``TODAY = datetime(2026, 6, 2)`` and
# generates every transaction as ``TODAY - 1..89 days``. Wall-clock "now" is
# later than that, so calling datetime.now() inside pipeline logic would:
#   1. make the trailing-30-day metrics return ZERO rows (the newest transaction
#      is already older than 30 real days), and
#   2. make TX-08 non-deterministic -- the three future-dated rows (+8/+16/+25
#      days) quietly become "historical" once the real calendar catches up, so
#      the same code would produce a different audit report tomorrow.
# Pinning the reference date is therefore both a correctness fix and what makes
# the whole run byte-reproducible. Never call datetime.now() in pipeline logic;
# it is used only to stamp "generated_at" metadata, which is not logic.
AS_OF_DATE: dt.date = dt.date(2026, 6, 2)

# ── Business / quality thresholds ─────────────────────────────────────────────
RETURN_RATE_ALERT_THRESHOLD: float = 0.10
"""A store whose unit-based return rate exceeds 10% is surfaced as an alert.
WHY 10%: it is the conventional retail red-line and, crucially, it is stated
here rather than buried in a SQL literal, so the business can move it."""

RECENT_WINDOW_DAYS: int = 30
"""Length of the trailing window for `top_stores_recent_30d`, measured backwards
from AS_OF_DATE (inclusive of the window's first day)."""

PRICE_TOLERANCE: float = 0.01
"""Dollar tolerance when reconciling ``quantity * unit_price`` against the
reported ``total_amount`` (TX-03).
WHY a tolerance at all: the source rounds money to 2dp and floats do not
represent cents exactly, so an exact ``!=`` comparison would flag hundreds of
clean rows as discounted. WHY one cent specifically: the seeded discounts are
5-20% of order value (dollars, not cents), so a one-cent floor separates real
discounts from float noise with an enormous margin -- there is no row anywhere
near the boundary."""

# ── Shared vocabulary ─────────────────────────────────────────────────────────
GUEST_CUSTOMER_ID: str = "GUEST"
"""Sentinel written into ``customer_id`` for TX-06 guest checkouts.
WHY a sentinel instead of NULL: dim_customer needs a non-null natural key and
fact_sales needs a non-null FK, but the 40 guest rows are real revenue that must
not be dropped. The paired ``is_guest`` flag is what lets analytics exclude them
from customer leaderboards while still counting their money everywhere else."""

ZIP_CODE_LENGTH: int = 5
"""Expected US ZIP width (ST-01). Named so the padding logic reads as intent."""

# ── F16 · quarantine disposition vocabulary ───────────────────────────────────
# WHY this exists: ``output/quarantine/`` held two different kinds of row under
# one name. Some rows were removed from the output (the duplicate P012, the
# losing S007 and P005 variants); others were *snapshots of rows that survive*,
# filed there so a data steward can review a decision (S003's padded ZIP, P027's
# imputed price, the P005 row that won). A reader summing the products quarantine
# CSVs therefore computed 32 - 4 = 28 and could not reconcile it against
# dim_product's 30 rows -- the arithmetic looked wrong when it was the labelling
# that was missing.
# DECISION: every quarantine CSV carries a ``disposition`` column drawn from this
# closed two-value vocabulary. Sum the ``dropped`` rows and the row budget
# reconciles exactly; the ``evidence`` rows are review items that changed nothing
# about the row count. ``transactions__lineage.csv`` already worked this way and
# is the pattern being generalised here.
# WHY a shared constant rather than string literals at each call site: the two
# cleaning modules must spell these identically or a reader filtering the CSVs
# gets a partial answer, and a typo in a literal is invisible until then.
DISPOSITION_DROPPED: str = "dropped"
"""The row is NOT in the cleaned output. It counts against the row budget."""

DISPOSITION_EVIDENCE: str = "evidence"
"""The row (or its business key) SURVIVES into the cleaned output; this copy is a
review snapshot of a decision that was made about it. It does not count against
the row budget."""

VALID_DISPOSITIONS: frozenset[str] = frozenset({DISPOSITION_DROPPED, DISPOSITION_EVIDENCE})
"""Closed vocabulary, so a third spelling cannot appear without a decision."""

MAX_AFFECTED_KEYS_SERIALIZED: int = 50
"""Cap on business keys embedded per defect record in audit_report.json.
WHY: TX-06 alone has 40 keys and TX-10 has 30; an uncapped list would make the
JSON unreadable for a human reviewer. The full set always survives in the
quarantine CSVs, so nothing is actually lost by truncating the summary."""

# ── Date formats accepted by the transaction date parser (TX-01) ──────────────
# WHY an ORDERED tuple and not a set: ISO must be attempted first because it is
# the only unambiguous form, and the two injected variants are separable by
# delimiter ('/' => US month-first, '-' with a 2-digit head => EU day-first).
# Order is the disambiguation rule, so it is data, not decoration.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",   # ISO-8601 -- the 465 well-formed rows
    "%m/%d/%Y",   # TX-01: 10 US-style rows
    "%d-%m-%Y",   # TX-01: 10 EU-style rows
)


# ── Per-run overrides ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RunConfig:
    """Immutable, per-invocation view of the settings above.

    ``pipeline.py`` builds one of these from argv and threads it through every
    stage. The module-level constants remain the defaults, so importing code
    (tests, notebooks, other agents' modules) never has to construct one.

    Attributes:
        as_of_date: Reference "today". Defaults to :data:`AS_OF_DATE`.
        raw_dir: Directory holding stores.csv / products.csv / transactions.csv.
        output_dir: Root for every generated artifact.
        skip_dashboard_export: When True, ``dashboard_bundle.json`` is not
            written. Useful in CI where only the audit report matters.

    Defects handled: none directly; carries AS_OF_DATE, on which TX-08 and all
        trailing-window analytics depend.
    """

    as_of_date: dt.date = AS_OF_DATE
    raw_dir: Path = field(default=RAW_DIR)
    output_dir: Path = field(default=OUTPUT_DIR)
    skip_dashboard_export: bool = False

    # WHY properties rather than stored fields: every derived path must follow
    # ``output_dir`` when the caller overrides it with --output-dir. Storing
    # them at construction time would let the two drift apart.
    @property
    def cleaned_dir(self) -> Path:
        return self.output_dir / "cleaned"

    @property
    def quarantine_dir(self) -> Path:
        return self.output_dir / "quarantine"

    @property
    def db_path(self) -> Path:
        return self.output_dir / "warehouse.db"

    @property
    def profile_report_path(self) -> Path:
        return self.output_dir / "profile_report.json"

    @property
    def audit_report_path(self) -> Path:
        return self.output_dir / "audit_report.json"

    @property
    def analytics_path(self) -> Path:
        return self.output_dir / "analytics.json"

    @property
    def defect_catalog_path(self) -> Path:
        return self.output_dir / "defect_catalog.json"

    @property
    def dashboard_bundle_path(self) -> Path:
        return self.output_dir / "dashboard_bundle.json"

    @property
    def recent_window_start(self) -> dt.date:
        """First day of the trailing window, inclusive.

        WHY ``- (RECENT_WINDOW_DAYS - 1)``: a 30-day window that includes
        AS_OF_DATE itself spans 30 calendar days, not 31. Off-by-one here would
        quietly change every "recent" number in the dashboard.
        """
        return self.as_of_date - dt.timedelta(days=RECENT_WINDOW_DAYS - 1)


DEFAULT_RUN_CONFIG: RunConfig = RunConfig()
"""Convenience singleton for callers that do not parse command-line arguments."""


__all__ = [
    "AS_OF_DATE",
    "ANALYTICS_PATH",
    "AUDIT_REPORT_PATH",
    "CLEANED_DIR",
    "DASHBOARD_BUNDLE_PATH",
    "DATE_FORMATS",
    "DB_PATH",
    "DEFAULT_RUN_CONFIG",
    "DEFECT_CATALOG_JSON_PATH",
    "DISPOSITION_DROPPED",
    "DISPOSITION_EVIDENCE",
    "GUEST_CUSTOMER_ID",
    "MAX_AFFECTED_KEYS_SERIALIZED",
    "OUTPUT_DIR",
    "PRICE_TOLERANCE",
    "PROFILE_REPORT_PATH",
    "QUARANTINE_DIR",
    "RAW_DIR",
    "RECENT_WINDOW_DAYS",
    "REPO_ROOT",
    "RETURN_RATE_ALERT_THRESHOLD",
    "RunConfig",
    "SCHEMA_SQL_PATH",
    "SRC_DIR",
    "VALID_DISPOSITIONS",
    "ZIP_CODE_LENGTH",
]
