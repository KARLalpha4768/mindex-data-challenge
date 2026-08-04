#!/usr/bin/env python3
"""Assert that every published figure in README.md still matches a live pipeline run.

WHY THIS FILE EXISTS
--------------------
An independent audit of an earlier revision of this submission found eight wrong
headline numbers in the README -- the top store, the top customer, and three of the
four figures in the revenue reconciliation the README itself presented as its
proof-of-work. None of them were arithmetic errors. They were *stale*: correct output
from an older pipeline, copied into prose by hand, and never revisited when the code
moved underneath them.

Hand-copied numbers rot silently, and a reviewer who spot-checks one figure and finds
it wrong stops trusting the ones that are right. The structural fix is not to be more
careful; it is to make documentation staleness a *build failure*. This script is that
gate.

HOW IT WORKS
------------
Every checked figure in README.md is wrapped in a pair of HTML comments::

    ... net revenue of <!-- fig:net_revenue -->$158,044.29<!-- /fig --> across ...

The markers are invisible in rendered Markdown. This script extracts the literal text
between them -- the exact characters a human reads -- normalises it, and compares it
against a value resolved live from the pipeline's own artifacts.

WHY CHECK THE RENDERED TEXT RATHER THAN A DATA BLOCK: a table of canonical values at
the bottom of the README would prove only that the table is current. The failure mode
being defended against is prose drifting away from the data, so the prose itself is
what must be asserted.

The mapping from marker id to live value lives in :data:`FIGURES` below, never in the
README. A number therefore cannot be "verified" against itself: the README supplies
the claim, this file supplies the address of the truth, and the artifacts supply the
truth.

SOURCES A FIGURE MAY BE RESOLVED FROM
-------------------------------------
``analytics``  ``<output>/analytics.json``           -- dotted path
``audit``      ``<output>/audit_report.json``        -- dotted path
``lineage``    ``<output>/quarantine/transactions__lineage.csv`` -- disposition counts
``db``         ``<output>/warehouse.db``             -- one of the named probes below
``config``     ``src/config.py``                     -- one of the named settings below
``pytest``     a live ``pytest --collect-only``      -- the test count

Usage:
    python scripts/check_readme_numbers.py                       # checks ./output
    python scripts/check_readme_numbers.py --output-dir /tmp/out
    python scripts/check_readme_numbers.py --readme /tmp/broken.md   # negative test
    python scripts/check_readme_numbers.py --list                 # show the registry

Exit codes:
    0  every marked figure matches the live artifacts
    1  at least one figure is stale, unknown, or unused
    2  the artifacts could not be read at all (run the pipeline first)

Defects handled: none directly. This is a documentation-integrity gate, and it exists
because of audit finding F1.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

# WHY sys.path surgery: this script is executed as a file (``python scripts/...``),
# not as a module, so the repo root is not on the path and ``import src.config``
# would fail. Inserting the parent of scripts/ makes the import behave exactly as it
# does inside the pipeline.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ══════════════════════════════════════════════════════════════════════════════
#  Marker syntax
# ══════════════════════════════════════════════════════════════════════════════
# WHY HTML comments rather than a bespoke syntax: they are invisible in every
# Markdown renderer (GitHub, VS Code preview, pandoc), they survive copy-paste, and
# they cannot collide with prose. WHY a paired open/close rather than a single
# annotation: the closing tag delimits exactly which characters are the claim, so the
# parser never has to guess where a number ends -- no regex-guessing at prose.
MARKER_RE = re.compile(
    r"<!--\s*fig:(?P<fid>[A-Za-z0-9_.\-]+)\s*-->"   # opening marker carries the id
    r"(?P<text>[^<]*)"                              # the visible claim (no nested tags)
    r"<!--\s*/fig\s*-->"                            # closing marker
)


# ══════════════════════════════════════════════════════════════════════════════
#  Named SQL probes  (source: db)
# ══════════════════════════════════════════════════════════════════════════════
# WHY the SQL lives here and not in the README: a figure must never be checkable
# against a query the document itself supplies, or the check becomes circular. Each
# probe must return exactly one row with exactly one column.
DB_PROBES: dict[str, str] = {
    # PR-02 -- the dimension carries the elected list price, the fact carries what
    # was actually transacted. These two probes are the price-separation claim.
    "p005_dim_price": "SELECT list_unit_price FROM dim_product WHERE product_id = 'P005'",
    "p005_fact_price": """
        SELECT DISTINCT f.unit_price FROM fact_sales f
        JOIN dim_product p USING (product_key) WHERE p.product_id = 'P005'
    """,
    "p005_fact_rows": """
        SELECT COUNT(*) FROM fact_sales f
        JOIN dim_product p USING (product_key) WHERE p.product_id = 'P005'
    """,
    "p005_price_delta": """
        SELECT ROUND(
            (SELECT list_unit_price FROM dim_product WHERE product_id = 'P005')
            - (SELECT DISTINCT f.unit_price FROM fact_sales f
               JOIN dim_product p USING (product_key) WHERE p.product_id = 'P005'), 2)
    """,
    # PR-04 -- same separation, imputed side.
    "p027_dim_price": "SELECT list_unit_price FROM dim_product WHERE product_id = 'P027'",
    "p027_fact_price": """
        SELECT DISTINCT f.unit_price FROM fact_sales f
        JOIN dim_product p USING (product_key) WHERE p.product_id = 'P027'
    """,
    # ST-01 / ST-03 -- the flags are stored, so they are assertable.
    "s003_zip": "SELECT zip_code FROM dim_store WHERE store_id = 'S003'",
    "s003_zip_suspect": "SELECT zip_is_suspect FROM dim_store WHERE store_id = 'S003'",
    "stores_region_imputed": "SELECT COUNT(*) FROM dim_store WHERE region_is_imputed = 1",
    "distinct_regions": "SELECT COUNT(DISTINCT region) FROM dim_store",
    # PR-03
    "products_category_imputed": "SELECT COUNT(*) FROM dim_product WHERE category_is_imputed = 1",
    # TX-06 -- guests are kept, and this is how much revenue that decision preserved.
    "guest_fact_rows": """
        SELECT COUNT(*) FROM fact_sales f JOIN dim_customer c USING (customer_key)
        WHERE c.is_guest = 1
    """,
    "guest_revenue": """
        SELECT ROUND(SUM(f.net_amount), 2) FROM fact_sales f
        JOIN dim_customer c USING (customer_key) WHERE c.is_guest = 1
    """,
    "real_customers": "SELECT COUNT(*) FROM dim_customer WHERE is_guest = 0",
    # TX-10
    "return_rows": "SELECT COUNT(*) FROM fact_sales WHERE is_return = 1",
    "return_units": "SELECT -SUM(quantity) FROM fact_sales WHERE is_return = 1",
    # TX-03
    "discount_rows": "SELECT COUNT(*) FROM fact_sales WHERE discount_amount > 0.01",
    "discount_share_pct": """
        SELECT ROUND(100.0 * SUM(discount_amount) / SUM(net_amount), 2) FROM fact_sales
    """,
    # Trailing-window analytics: the row population the 30-day metric sees.
    "window_fact_rows": """
        SELECT COUNT(*) FROM fact_sales f JOIN dim_date d USING (date_key)
        WHERE d.full_date BETWEEN :window_start AND :as_of
    """,
    # F10 -- the alternative return-rate denominator, so the README's statement of
    # what the contracted formula *would* have produced is itself checked.
    "s006_contract_rate_pct": """
        SELECT ROUND(100.0 *
            SUM(CASE WHEN is_return = 1 THEN -quantity ELSE 0 END) /
            SUM(CASE WHEN is_return = 0 THEN  quantity ELSE 0 END), 2)
        FROM fact_sales f JOIN dim_store s USING (store_key) WHERE s.store_id = 'S006'
    """,
    # F9 -- the partial-month caveat, stated as two checkable integers.
    "days_2026_03": """
        SELECT COUNT(DISTINCT d.full_date) FROM fact_sales f JOIN dim_date d
        USING (date_key) WHERE d.year_month = '2026-03'
    """,
    "days_2026_06": """
        SELECT COUNT(DISTINCT d.full_date) FROM fact_sales f JOIN dim_date d
        USING (date_key) WHERE d.year_month = '2026-06'
    """,
    # WHY joined to the fact rather than read off dim_date: dim_date is dense to
    # AS_OF_DATE by design, so its MAX answers "how far does the calendar run", not
    # "when did the last sale happen". The README claims the latter.
    "first_txn_date": "SELECT MIN(d.full_date) FROM fact_sales f JOIN dim_date d USING (date_key)",
    "last_txn_date": "SELECT MAX(d.full_date) FROM fact_sales f JOIN dim_date d USING (date_key)",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Named config settings  (source: config)
# ══════════════════════════════════════════════════════════════════════════════
def _config_values() -> dict[str, Any]:
    """Read the live values out of ``src/config.py``.

    Returns:
        Mapping of probe name to value.

    WHY import rather than parse: the window start is *derived*
    (``as_of - (RECENT_WINDOW_DAYS - 1)``), so a text scrape would miss an off-by-one
    introduced in the derivation -- which is the exact boundary the README documents.
    """
    from src import config as cfg  # noqa: PLC0415 -- deliberately late, see sys.path above

    # RunConfig owns the derivation; asking it is what makes the boundary claim in the
    # README a statement about the code rather than about a comment in the code.
    window_start = cfg.RunConfig().recent_window_start

    return {
        "as_of_date": cfg.AS_OF_DATE.isoformat(),
        "window_start": window_start.isoformat(),
        "window_days": cfg.RECENT_WINDOW_DAYS,
        "return_threshold_pct": round(cfg.RETURN_RATE_ALERT_THRESHOLD * 100, 2),
        "price_tolerance": cfg.PRICE_TOLERANCE,
        "guest_sentinel": cfg.GUEST_CUSTOMER_ID,
        "zip_length": cfg.ZIP_CODE_LENGTH,
        "date_format_count": len(cfg.DATE_FORMATS),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  The registry: marker id -> where the truth lives
# ══════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class FigureSpec:
    """One checkable figure.

    Attributes:
        source: Which artifact resolves it -- analytics | audit | lineage | db |
            config | pytest.
        path: Dotted path (analytics/audit), probe name (db/config), disposition
            selector (lineage), or ``test_count`` (pytest).
        kind: How the README text is normalised before comparison --
            money | number | percent | int | text.
        note: Why this figure is worth pinning. Shown by ``--list``.
    """

    source: str
    path: str
    kind: str
    note: str = ""


# Dotted-path grammar for ``analytics`` and ``audit``:
#   plain segment      -> dict key
#   integer segment    -> list index
#   ``field=value``    -> the element of a list whose ``field`` equals ``value``
# The last form is what keeps this registry readable: selecting the Northeast row by
# its region rather than by the position it happens to sort into today.
RECON = "metrics.revenue_reconciliation.rows.0"
TOP_STORES = "metrics.top_stores_recent_30d.rows"
AOV = "metrics.aov_by_region.rows"
RETURNS = "metrics.return_rate_by_store.rows"
CUSTOMERS = "metrics.top_customers_lifetime.rows"
MOM = "metrics.mom_growth_by_category.rows"
COUNTS = "pipeline.row_counts"

FIGURES: dict[str, FigureSpec] = {
    # ── Reference date and window convention (F18) ────────────────────────────
    "as_of_date": FigureSpec("config", "as_of_date", "text", "The single reference 'today'"),
    "window_start": FigureSpec("config", "window_start", "text", "Derived, not hardcoded"),
    "window_days": FigureSpec("config", "window_days", "int", "30-day window length"),
    "window_rows": FigureSpec("db", "window_fact_rows", "int", "Rows the window sees"),
    "return_threshold_pct": FigureSpec("config", "return_threshold_pct", "percent", "Alert level"),
    "price_tolerance": FigureSpec("config", "price_tolerance", "money", "TX-03 breach threshold"),
    "date_format_count": FigureSpec("config", "date_format_count", "int", "TX-01 format ladder"),
    "first_date": FigureSpec("db", "first_txn_date", "text", "Extract start"),
    "last_date": FigureSpec("db", "last_txn_date", "text", "Extract end"),

    # ── Row budget: 505 rows fully accounted for ──────────────────────────────
    "raw_stores": FigureSpec("audit", f"{COUNTS}.raw.stores", "int"),
    "raw_products": FigureSpec("audit", f"{COUNTS}.raw.products", "int"),
    "raw_transactions": FigureSpec("audit", f"{COUNTS}.raw.transactions", "int"),
    "clean_stores": FigureSpec("audit", f"{COUNTS}.cleaned.stores", "int"),
    "clean_products": FigureSpec("audit", f"{COUNTS}.cleaned.products", "int"),
    "clean_transactions": FigureSpec("audit", f"{COUNTS}.cleaned.transactions", "int"),
    "quarantine_csv_rows": FigureSpec("audit", "totals.rows_quarantined", "int",
                                      "All quarantine CSV rows, drops AND evidence"),
    "lineage_kept": FigureSpec("lineage", "kept", "int"),
    "lineage_quarantined": FigureSpec("lineage", "quarantined", "int"),
    "lineage_dropped": FigureSpec("lineage", "dropped", "int"),
    "lineage_total": FigureSpec("lineage", "total", "int"),
    "lineage_tx04": FigureSpec("lineage", "reason.TX-04", "int"),
    "lineage_tx05": FigureSpec("lineage", "reason.TX-05", "int"),
    "lineage_tx07": FigureSpec("lineage", "reason.TX-07", "int"),
    "lineage_tx08": FigureSpec("lineage", "reason.TX-08", "int"),
    "lineage_tx09": FigureSpec("lineage", "reason.TX-09", "int"),

    # ── Warehouse ─────────────────────────────────────────────────────────────
    "dim_date_rows": FigureSpec("audit", f"{COUNTS}.warehouse.dim_date", "int"),
    "dim_store_rows": FigureSpec("audit", f"{COUNTS}.warehouse.dim_store", "int"),
    "dim_product_rows": FigureSpec("audit", f"{COUNTS}.warehouse.dim_product", "int"),
    "dim_customer_rows": FigureSpec("audit", f"{COUNTS}.warehouse.dim_customer", "int"),
    "fact_rows": FigureSpec("audit", f"{COUNTS}.warehouse.fact_sales", "int"),
    "fk_violations": FigureSpec("audit", f"{COUNTS}.warehouse.fk_violations", "int"),
    "tie_out_cents": FigureSpec("audit", f"{COUNTS}.warehouse.revenue_tie_out_cents", "int"),
    "real_customers": FigureSpec("db", "real_customers", "int", "dim_customer minus GUEST"),
    "distinct_regions": FigureSpec("db", "distinct_regions", "int", "4 -- no invented 'East'"),

    # ── Defect coverage ───────────────────────────────────────────────────────
    "defect_classes": FigureSpec("audit", "totals.defect_classes_in_catalog", "int"),
    "defects_detected": FigureSpec("audit", "totals.defect_classes_detected", "int"),
    "defect_mismatches": FigureSpec("audit", "totals.mismatch_count", "int"),
    "rows_affected": FigureSpec("audit", "totals.rows_affected_total", "int"),

    # ── Per-defect detected counts (the decision matrix) ──────────────────────
    **{
        f"det_{code}": FigureSpec("audit", f"records.code={code}.detected_count", "int")
        for code in (
            "ST-01", "ST-02", "ST-03",
            "PR-01", "PR-02", "PR-03", "PR-04",
            "TX-01", "TX-02", "TX-03", "TX-04", "TX-05",
            "TX-06", "TX-07", "TX-08", "TX-09", "TX-10",
        )
    },

    # ── Revenue reconciliation ────────────────────────────────────────────────
    "gross_list_value": FigureSpec("analytics", f"{RECON}.gross_list_value", "money"),
    "discount_total": FigureSpec("analytics", f"{RECON}.discount_total", "money"),
    "gross_net_of_discount": FigureSpec("analytics", f"{RECON}.gross_sales_net_of_discount", "money"),
    "returns_value": FigureSpec("analytics", f"{RECON}.returns_value", "money"),
    "net_revenue": FigureSpec("analytics", f"{RECON}.net_revenue", "money"),
    "line_level_delta": FigureSpec("analytics", f"{RECON}.line_level_delta", "money"),
    "aggregate_delta": FigureSpec("analytics", f"{RECON}.aggregate_delta", "money"),
    "discount_rows": FigureSpec("db", "discount_rows", "int"),
    "discount_share_pct": FigureSpec("db", "discount_share_pct", "percent"),

    # ── Top stores, trailing 30 days ──────────────────────────────────────────
    "store1_name": FigureSpec("analytics", f"{TOP_STORES}.0.store_name", "text"),
    "store1_region": FigureSpec("analytics", f"{TOP_STORES}.0.region", "text"),
    "store1_revenue": FigureSpec("analytics", f"{TOP_STORES}.0.net_revenue", "money"),
    "store1_txns": FigureSpec("analytics", f"{TOP_STORES}.0.transaction_count", "int"),
    "store2_name": FigureSpec("analytics", f"{TOP_STORES}.1.store_name", "text"),
    "store2_revenue": FigureSpec("analytics", f"{TOP_STORES}.1.net_revenue", "money"),
    "store3_name": FigureSpec("analytics", f"{TOP_STORES}.2.store_name", "text"),
    "store3_revenue": FigureSpec("analytics", f"{TOP_STORES}.2.net_revenue", "money"),
    "store4_name": FigureSpec("analytics", f"{TOP_STORES}.3.store_name", "text"),
    "store4_revenue": FigureSpec("analytics", f"{TOP_STORES}.3.net_revenue", "money"),
    "store5_name": FigureSpec("analytics", f"{TOP_STORES}.4.store_name", "text"),
    "store5_revenue": FigureSpec("analytics", f"{TOP_STORES}.4.net_revenue", "money"),

    # ── AOV by region ─────────────────────────────────────────────────────────
    "aov_northeast": FigureSpec("analytics", f"{AOV}.region=Northeast.avg_transaction_value", "money"),
    "aov_south": FigureSpec("analytics", f"{AOV}.region=South.avg_transaction_value", "money"),
    "aov_midwest": FigureSpec("analytics", f"{AOV}.region=Midwest.avg_transaction_value", "money"),
    "aov_west": FigureSpec("analytics", f"{AOV}.region=West.avg_transaction_value", "money"),
    "aov_northeast_txns": FigureSpec("analytics", f"{AOV}.region=Northeast.transaction_count", "int"),
    "aov_south_txns": FigureSpec("analytics", f"{AOV}.region=South.transaction_count", "int"),
    "aov_midwest_txns": FigureSpec("analytics", f"{AOV}.region=Midwest.transaction_count", "int"),
    "aov_west_txns": FigureSpec("analytics", f"{AOV}.region=West.transaction_count", "int"),

    # ── Return rate ───────────────────────────────────────────────────────────
    "s006_unit_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S006.unit_return_rate_pct", "percent"),
    "s015_unit_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S015.unit_return_rate_pct", "percent"),
    "s008_unit_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S008.unit_return_rate_pct", "percent"),
    "s006_txn_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S006.txn_return_rate_pct", "percent"),
    "s015_txn_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S015.txn_return_rate_pct", "percent"),
    "s008_txn_rate": FigureSpec("analytics", f"{RETURNS}.store_id=S008.txn_return_rate_pct", "percent"),
    "s006_units_sold": FigureSpec("analytics", f"{RETURNS}.store_id=S006.units_sold", "int"),
    "s006_units_returned": FigureSpec("analytics", f"{RETURNS}.store_id=S006.units_returned", "int"),
    "s006_contract_rate": FigureSpec("db", "s006_contract_rate_pct", "percent",
                                     "What contract §6's denominator would have given"),
    "stores_scored": FigureSpec("analytics", "metrics.return_rate_by_store.row_count", "int"),

    # ── Top customers ─────────────────────────────────────────────────────────
    "cust1_id": FigureSpec("analytics", f"{CUSTOMERS}.0.customer_id", "text"),
    "cust1_spend": FigureSpec("analytics", f"{CUSTOMERS}.0.lifetime_spend", "money"),
    "cust1_txns": FigureSpec("analytics", f"{CUSTOMERS}.0.transaction_count", "int"),
    "cust2_id": FigureSpec("analytics", f"{CUSTOMERS}.1.customer_id", "text"),
    "cust2_spend": FigureSpec("analytics", f"{CUSTOMERS}.1.lifetime_spend", "money"),
    "cust3_id": FigureSpec("analytics", f"{CUSTOMERS}.2.customer_id", "text"),
    "cust3_spend": FigureSpec("analytics", f"{CUSTOMERS}.2.lifetime_spend", "money"),

    # ── Month over month (F9 caveat, made checkable) ──────────────────────────
    "mom_rows": FigureSpec("analytics", "metrics.mom_growth_by_category.row_count", "int"),
    "days_march": FigureSpec("db", "days_2026_03", "int"),
    "days_june": FigureSpec("db", "days_2026_06", "int"),
    "fb_april_pct": FigureSpec(
        "analytics", f"{MOM}.category=Food & Beverage;year_month=2026-04.mom_change_pct", "percent",
        "The +403% that is a boundary artefact, not growth",
    ),
    "apparel_june_pct": FigureSpec(
        "analytics", f"{MOM}.category=Apparel;year_month=2026-06.mom_change_pct", "percent",
        "The -95.92% produced by a one-day month",
    ),
    "apparel_may_pct": FigureSpec(
        "analytics", f"{MOM}.category=Apparel;year_month=2026-05.mom_change_pct", "percent",
        "A reading from the only two complete months",
    ),
    "apparel_june_days": FigureSpec(
        "analytics", f"{MOM}.category=Apparel;year_month=2026-06.days_with_data", "int",
    ),

    # ── The three judgement calls ─────────────────────────────────────────────
    "p005_dim_price": FigureSpec("db", "p005_dim_price", "money"),
    "p005_fact_price": FigureSpec("db", "p005_fact_price", "money"),
    "p005_fact_rows": FigureSpec("db", "p005_fact_rows", "int"),
    "p005_price_delta": FigureSpec("db", "p005_price_delta", "money"),
    "p027_dim_price": FigureSpec("db", "p027_dim_price", "money"),
    "p027_fact_price": FigureSpec("db", "p027_fact_price", "money"),
    "s003_zip": FigureSpec("db", "s003_zip", "text"),
    "s003_zip_suspect": FigureSpec("db", "s003_zip_suspect", "int"),
    "regions_imputed": FigureSpec("db", "stores_region_imputed", "int"),
    "categories_imputed": FigureSpec("db", "products_category_imputed", "int"),
    "guest_rows": FigureSpec("db", "guest_fact_rows", "int"),
    "guest_revenue": FigureSpec("db", "guest_revenue", "money"),
    "return_rows": FigureSpec("db", "return_rows", "int"),
    "return_units": FigureSpec("db", "return_units", "int"),

    # ── Test suite ────────────────────────────────────────────────────────────
    "test_count": FigureSpec("pytest", "test_count", "int", "Live collection, not a memory"),
}


# ══════════════════════════════════════════════════════════════════════════════
#  Normalisation and comparison
# ══════════════════════════════════════════════════════════════════════════════
# WHY a tolerance at all: currency is serialised as an IEEE-754 double, and the README
# prints two decimals. Half a cent is tighter than any figure this pipeline publishes
# and looser than float noise.
MONEY_TOLERANCE = 0.005

_NUMERIC_STRIP = str.maketrans(
    # The last two entries are a regular space and U+00A0 NO-BREAK SPACE: both
    # render identically, and a figure typed with either must still compare equal.
    {"$": "", ",": "", "%": "", "+": "", " ": "", "\u00a0": ""}
)


def normalise_number(text: str) -> float:
    """Turn a rendered figure such as ``-$9,952.03`` into ``-9952.03``.

    Args:
        text: The literal characters between the fig markers.

    Returns:
        The numeric value.

    Raises:
        ValueError: If the text is not a number once decorations are removed.

    WHY the unicode replacements: an em-dash minus (U+2212) and a non-breaking space
    both render identically to their ASCII forms, so a figure typed with either must
    still compare equal rather than failing as "not a number".
    """
    cleaned = text.strip().replace("−", "-").replace("–", "-")
    negative_parens = cleaned.startswith("(") and cleaned.endswith(")")
    if negative_parens:
        cleaned = cleaned[1:-1]
    cleaned = cleaned.translate(_NUMERIC_STRIP)
    value = float(cleaned)
    return -value if negative_parens else value


def compare(kind: str, claimed_text: str, actual: Any) -> tuple[bool, str, str]:
    """Compare one README claim against one live value.

    Args:
        kind: money | number | percent | int | text.
        claimed_text: What the README says.
        actual: What the artifact says.

    Returns:
        ``(ok, claimed_display, actual_display)``.
    """
    if kind == "text":
        claimed = " ".join(claimed_text.split())
        expected = " ".join(str(actual).split())
        return claimed == expected, claimed, expected

    claimed_value = normalise_number(claimed_text)
    actual_value = float(actual)

    if kind == "int":
        # WHY exact: a row count that is off by one is a real defect, not rounding.
        ok = abs(claimed_value - actual_value) < 1e-9
        return ok, f"{claimed_value:g}", f"{actual_value:g}"

    ok = abs(claimed_value - actual_value) <= MONEY_TOLERANCE
    return ok, f"{claimed_value:,.2f}", f"{actual_value:,.2f}"


# ══════════════════════════════════════════════════════════════════════════════
#  Resolvers
# ══════════════════════════════════════════════════════════════════════════════
def resolve_path(document: Any, path: str) -> Any:
    """Walk a dotted path through nested JSON.

    Supports three segment forms: a dict key, an integer list index, and
    ``field=value`` (several joined by ``;``) which selects the first element of a
    list matching every stated field.

    Args:
        document: Parsed JSON.
        path: e.g. ``metrics.aov_by_region.rows.region=Northeast.avg_transaction_value``.

    Returns:
        The addressed value.

    Raises:
        KeyError: If any segment does not resolve. The message names the exact
            segment, because a silent ``None`` here would let a stale figure pass.
    """
    node = document
    walked: list[str] = []
    for segment in path.split("."):
        walked.append(segment)
        where = ".".join(walked)
        if "=" in segment and isinstance(node, list):
            # WHY a compound selector: mom_growth_by_category is keyed by category AND
            # month, so a single field would silently select whichever row sorts first
            # -- the class of bug this whole script exists to prevent.
            wanted = dict(part.split("=", 1) for part in segment.split(";"))
            match = next(
                (el for el in node
                 if all(str(el.get(field)) == value for field, value in wanted.items())),
                None,
            )
            if match is None:
                raise KeyError(f"no element matching {wanted} at {where}")
            node = match
        elif segment.isdigit() and isinstance(node, list):
            index = int(segment)
            if index >= len(node):
                raise KeyError(f"index {index} out of range at {where}")
            node = node[index]
        elif isinstance(node, dict):
            if segment not in node:
                raise KeyError(f"missing key {segment!r} at {where}")
            node = node[segment]
        else:
            raise KeyError(f"cannot descend into {type(node).__name__} at {where}")
    return node


def lineage_counts(lineage_csv: Path) -> dict[str, int]:
    """Summarise the per-source-row disposition ledger.

    Args:
        lineage_csv: ``quarantine/transactions__lineage.csv`` -- one row per source row.

    Returns:
        Counts keyed by ``kept`` / ``quarantined`` / ``dropped`` / ``total`` and by
        ``reason.<CODE>``.

    WHY csv rather than pandas: this gate must be runnable in CI with nothing but the
    standard library, so a documentation check can never be the thing that fails for
    want of a dependency.
    """
    import csv

    counts: dict[str, int] = {"kept": 0, "quarantined": 0, "dropped": 0, "total": 0}
    with lineage_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            counts["total"] += 1
            counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
            code = (row.get("reason_code") or "").strip()
            if code:
                counts[f"reason.{code}"] = counts.get(f"reason.{code}", 0) + 1
    return counts


def db_probe(db_path: Path, name: str, params: dict[str, Any]) -> Any:
    """Run one named probe against the warehouse.

    Args:
        db_path: ``warehouse.db``.
        name: Key into :data:`DB_PROBES`.
        params: Named parameters available to every probe.

    Returns:
        The single scalar the probe selects.

    Raises:
        KeyError: Unknown probe name.
        ValueError: The probe returned zero or more than one row.
    """
    sql = DB_PROBES[name]
    bound = {k: v for k, v in params.items() if f":{k}" in sql}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        rows = conn.execute(sql, bound).fetchall()
    if len(rows) != 1:
        raise ValueError(f"probe {name!r} returned {len(rows)} rows; expected exactly 1")
    return rows[0][0]


def pytest_test_count() -> int:
    """Count the test suite by collecting it, not by remembering it.

    Returns:
        Number of collected tests.

    Raises:
        RuntimeError: If pytest is unavailable or collection fails.

    WHY ``-o addopts=``: pyproject sets ``-q``, and a second ``-q`` suppresses the
    "N tests collected" summary line this parses. Clearing addopts makes the output
    independent of project configuration.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts="],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if not match:
        raise RuntimeError(f"could not parse pytest collection output:\n{proc.stdout[-500:]}")
    return int(match.group(1))


# ══════════════════════════════════════════════════════════════════════════════
#  Driver
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Sources:
    """Lazily-loaded live artifacts, plus the resolver for each source name."""

    output_dir: Path
    _cache: dict[str, Any]

    def json_file(self, name: str) -> Any:
        if name not in self._cache:
            path = self.output_dir / name
            if not path.exists():
                raise FileNotFoundError(path)
            self._cache[name] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[name]

    def resolve(self, spec: FigureSpec) -> Any:
        if spec.source == "analytics":
            return resolve_path(self.json_file("analytics.json"), spec.path)
        if spec.source == "audit":
            return resolve_path(self.json_file("audit_report.json"), spec.path)
        if spec.source == "lineage":
            if "lineage" not in self._cache:
                self._cache["lineage"] = lineage_counts(
                    self.output_dir / "quarantine" / "transactions__lineage.csv"
                )
            counts = self._cache["lineage"]
            if spec.path not in counts:
                raise KeyError(f"lineage has no disposition {spec.path!r}")
            return counts[spec.path]
        if spec.source == "config":
            if "config" not in self._cache:
                self._cache["config"] = _config_values()
            return self._cache["config"][spec.path]
        if spec.source == "db":
            if "config" not in self._cache:
                self._cache["config"] = _config_values()
            cfg = self._cache["config"]
            return db_probe(
                self.output_dir / "warehouse.db",
                spec.path,
                {"window_start": cfg["window_start"], "as_of": cfg["as_of_date"]},
            )
        if spec.source == "pytest":
            if "pytest" not in self._cache:
                self._cache["pytest"] = pytest_test_count()
            return self._cache["pytest"]
        raise KeyError(f"unknown source {spec.source!r}")


def extract_claims(readme: Path) -> list[tuple[str, str]]:
    """Pull every ``<!-- fig:id -->text<!-- /fig -->`` pair out of the README.

    Args:
        readme: Path to the document.

    Returns:
        ``(id, visible_text)`` in document order. Ids may repeat -- the same figure is
        often cited in more than one place, and every occurrence is checked.
    """
    text = readme.read_text(encoding="utf-8")
    return [(m.group("fid"), m.group("text")) for m in MARKER_RE.finditer(text)]


def run(readme: Path, output_dir: Path, verbose: bool) -> int:
    """Check the README against the artifacts.

    Args:
        readme: Document to check.
        output_dir: Directory holding a completed pipeline run.
        verbose: Print passing figures too, not just failures.

    Returns:
        Process exit code: 0 clean, 1 mismatches, 2 artifacts unreadable.
    """
    claims = extract_claims(readme)
    if not claims:
        print(f"FAIL  {readme} contains no fig: markers at all.")
        return 1

    sources = Sources(output_dir=output_dir, _cache={})
    failures: list[str] = []
    checked = 0

    for fid, claimed_text in claims:
        spec = FIGURES.get(fid)
        if spec is None:
            failures.append(
                f"unknown figure id 'fig:{fid}' -- add it to FIGURES in "
                f"scripts/check_readme_numbers.py or remove the marker"
            )
            continue
        try:
            actual = sources.resolve(spec)
        except FileNotFoundError as exc:
            print(f"FATAL  missing artifact {exc}. Run the pipeline first:")
            print("       python -m src.pipeline")
            return 2
        except Exception as exc:  # noqa: BLE001 - the message is the product here
            failures.append(f"fig:{fid}  could not resolve {spec.source}:{spec.path} -- {exc}")
            continue

        ok, claimed_display, actual_display = compare(spec.kind, claimed_text, actual)
        checked += 1
        if ok:
            if verbose:
                print(f"  ok    fig:{fid:<24} {claimed_display}")
        else:
            failures.append(
                f"fig:{fid}  README says {claimed_display!r} but "
                f"{spec.source}:{spec.path} says {actual_display!r}"
            )

    # WHY also fail on unused registry entries: an id that no longer appears in the
    # README usually means a paragraph was deleted or a marker was broken during an
    # edit, and the figure quietly stopped being checked. Silence is the failure mode
    # this whole script exists to remove.
    used = {fid for fid, _ in claims}
    unused = sorted(set(FIGURES) - used)
    for fid in unused:
        failures.append(f"fig:{fid} is registered but never cited in {readme.name}")

    print()
    print(f"README      {readme}")
    print(f"artifacts   {output_dir}")
    print(f"figures     {len(claims)} marked, {len(FIGURES)} registered, {checked} compared")

    if failures:
        print(f"RESULT      FAIL -- {len(failures)} problem(s)")
        for line in failures:
            print(f"  FAIL  {line}")
        return 1

    print("RESULT      PASS -- every published figure matches the live run")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Assert README.md figures against a live pipeline run.",
        epilog="Exit 0 = all figures current, 1 = at least one stale, 2 = no artifacts.",
    )
    parser.add_argument("--readme", type=Path, default=REPO_ROOT / "README.md",
                        help="Document to check (default: ./README.md).")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "output",
                        help="Directory holding a completed run (default: ./output).")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print every figure, not just the failures.")
    parser.add_argument("--list", action="store_true",
                        help="Print the figure registry and exit.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list:
        for fid, spec in FIGURES.items():
            note = f"  # {spec.note}" if spec.note else ""
            print(f"{fid:<28} {spec.source:<9} {spec.kind:<8} {spec.path}{note}")
        return 0

    return run(args.readme.resolve(), args.output_dir.resolve(), args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
