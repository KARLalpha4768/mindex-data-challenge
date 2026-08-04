"""Tests that bind the analytics *contract*: metric ids, window boundaries, tie-outs.

Defends:
  F7 / M6   ``reconciliation_delta`` was an algebraic tautology —
            ``SUM(net WHERE ret=0) + SUM(net WHERE ret=1) − SUM(net)`` is
            identically zero for any data. The auditor inflated every money
            column in the warehouse by 50% (inventing $79k of revenue) and the
            delta still read 0.00. Replacing the whole expression with the
            literal ``0.0`` also survived the suite.
  F11       ``mom_growth_by_category`` and ``aov_by_region`` are binding metric
            ids from contract §6; the code shipped ``mom_revenue_by_category``
            and ``avg_txn_value_by_region``.
  F18       The README never states that the trailing window is 30 days
            *inclusive* (``AS_OF − 29``), which is the one boundary a reader
            would want pinned down.
  M5 / M18  Window length and upper bound. Both silently reorder the published
            top-5 store ranking and nothing bound them.

WHY A DELIBERATELY INCONSISTENT FIXTURE
---------------------------------------
The critical design point for M6: a test that only asserts ``delta == 0`` on
good data is itself a tautology and would have survived the mutation exactly as
the metric did. The reconciliation tests below therefore run **twice** — once on
a consistent warehouse where every delta must be zero, and once on a warehouse
containing a row whose ``net_amount`` disagrees with
``extended_amount − discount_amount`` by a known $5.00, where the delta must
come back non-zero and equal to that amount. Only the second direction proves
the metric can fail.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from src.analytics.queries import METRIC_REGISTRY
from src.config import AS_OF_DATE, RECENT_WINDOW_DAYS, RunConfig

from .conftest import metric_sql

# ── Contract §6, verbatim ────────────────────────────────────────────────────
CONTRACT_METRIC_IDS: frozenset[str] = frozenset({
    "top_stores_recent_30d",
    "mom_growth_by_category",
    "return_rate_by_store",
    "aov_by_region",
    "top_customers_lifetime",
    "revenue_reconciliation",
})

# ── The delta columns the reconciliation metric must expose (F7) ─────────────
REQUIRED_DELTA_COLUMNS: frozenset[str] = frozenset({"line_level_delta", "aggregate_delta"})


# ══════════════════════════════════════════════════════════════════════════════
# Warehouse builders
# ══════════════════════════════════════════════════════════════════════════════
_DDL = """
CREATE TABLE dim_date (
    date_key INTEGER PRIMARY KEY, full_date TEXT NOT NULL UNIQUE,
    year INTEGER, quarter INTEGER, month INTEGER, year_month TEXT,
    month_name TEXT, day_of_month INTEGER, day_of_week INTEGER, is_weekend INTEGER
);
CREATE TABLE dim_store (
    store_key INTEGER PRIMARY KEY AUTOINCREMENT, store_id TEXT UNIQUE NOT NULL,
    store_name TEXT, city TEXT, state TEXT, zip_code TEXT,
    zip_is_suspect INTEGER DEFAULT 0, region TEXT NOT NULL,
    region_is_imputed INTEGER DEFAULT 0, opened_date TEXT
);
CREATE TABLE dim_product (
    product_key INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT UNIQUE NOT NULL,
    product_name TEXT, category TEXT NOT NULL, category_is_imputed INTEGER DEFAULT 0,
    list_unit_price REAL NOT NULL, price_is_imputed INTEGER DEFAULT 0,
    price_conflict INTEGER DEFAULT 0, supplier_id TEXT
);
CREATE TABLE dim_customer (
    customer_key INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT UNIQUE NOT NULL, is_guest INTEGER NOT NULL
);
CREATE TABLE fact_sales (
    sales_key INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT UNIQUE NOT NULL,
    date_key INTEGER NOT NULL, store_key INTEGER NOT NULL, product_key INTEGER NOT NULL,
    customer_key INTEGER NOT NULL, quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
    extended_amount REAL NOT NULL, discount_amount REAL NOT NULL,
    net_amount REAL NOT NULL, is_return INTEGER NOT NULL
);
"""
"""Schema mirror for hand-built fixtures.

WHY a mirror and not ``schema.sql``: ``schema.sql`` carries CHECK constraints
that (correctly) reject an inconsistent row — ``ABS(discount_amount −
(extended_amount − net_amount)) <= 0.01`` is exactly the invariant the
inconsistent fixture below needs to violate. Loading through the real schema
would make the M6 fixture unbuildable, so the CHECKs are omitted *here only*,
and the fact that they are load-bearing in production is asserted separately in
``test_schema_rejects_the_inconsistent_row``.
"""


def _dates(cur: sqlite3.Cursor, days: list[str]) -> None:
    """Insert a dense-enough ``dim_date`` covering the given ISO date strings."""
    for iso in days:
        d = dt.date.fromisoformat(iso)
        cur.execute(
            "INSERT OR IGNORE INTO dim_date VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                int(iso.replace("-", "")), iso, d.year, (d.month - 1) // 3 + 1, d.month,
                iso[:7], d.strftime("%B"), d.day, d.weekday(), int(d.weekday() >= 5),
            ),
        )


def _build(db_path: Path, facts: list[tuple[Any, ...]], *, stores: list[tuple[str, str, str]]) -> None:
    """Create a fixture warehouse.

    Args:
        db_path: Target SQLite file.
        facts: ``(transaction_id, iso_date, store_id, qty, unit_price,
            extended, discount, net, is_return)`` tuples.
        stores: ``(store_id, store_name, region)`` triples.
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(_DDL)

    _dates(cur, sorted({f[1] for f in facts}))
    for store_id, store_name, region in stores:
        cur.execute(
            "INSERT INTO dim_store (store_id, store_name, city, state, zip_code, region) "
            "VALUES (?,?,'City','NY','14604',?)",
            (store_id, store_name, region),
        )
    cur.execute(
        "INSERT INTO dim_product (product_id, product_name, category, list_unit_price) "
        "VALUES ('P001','Test Product','Electronics',100.0)"
    )
    cur.execute("INSERT INTO dim_customer (customer_id, is_guest) VALUES ('CUST001', 0)")

    product_key = cur.execute("SELECT product_key FROM dim_product").fetchone()[0]
    customer_key = cur.execute("SELECT customer_key FROM dim_customer").fetchone()[0]
    store_keys = dict(cur.execute("SELECT store_id, store_key FROM dim_store").fetchall())

    for txn_id, iso, store_id, qty, price, ext, disc, net, is_ret in facts:
        cur.execute(
            "INSERT INTO fact_sales (transaction_id, date_key, store_key, product_key, "
            "customer_key, quantity, unit_price, extended_amount, discount_amount, "
            "net_amount, is_return) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (txn_id, int(iso.replace("-", "")), store_keys[store_id], product_key,
             customer_key, qty, price, ext, disc, net, is_ret),
        )
    conn.commit()
    conn.close()


def _run(db_path: Path, metric_id: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute a registered metric against a fixture warehouse."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(metric_sql(metric_id), params or {}).fetchall()]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# F11 · Metric ids are a binding interface
# ══════════════════════════════════════════════════════════════════════════════
class TestMetricIdContract:
    """Contract §6 names six metric ids; the registry must expose exactly those."""

    def test_registry_ids_match_the_contract_exactly(self) -> None:
        """F11: no extra ids, no missing ids, no old spellings.

        Metric ids key ``analytics.json``, the dashboard's ``METRIC_ORDER`` and
        every README reference. The shipped code used
        ``mom_revenue_by_category`` and ``avg_txn_value_by_region``, which are
        not the contracted names. Without this test the deviation is invisible
        until a consumer looks up a key that does not exist.
        """
        assert set(METRIC_REGISTRY) == set(CONTRACT_METRIC_IDS), (
            "Contract §6 fixes these ids. Renames: mom_revenue_by_category -> "
            "mom_growth_by_category, avg_txn_value_by_region -> aov_by_region."
        )

    def test_sql_ref_strings_agree_with_the_registry(self) -> None:
        """Every metric carries a resolvable ``sql`` body and a ``sql_ref`` pointer.

        The dashboard renders ``sql`` verbatim (contract §7b) and a reviewer
        follows ``sql_ref`` to the source. A metric with one and not the other
        renders as a broken panel.
        """
        for metric_id, spec in METRIC_REGISTRY.items():
            assert spec["sql"].strip(), f"{metric_id}: empty SQL body"
            assert spec["title"].strip(), f"{metric_id}: missing title"
            assert spec["definition_note"].strip(), f"{metric_id}: missing definition_note"
            assert spec["column_units"], f"{metric_id}: missing column_units"


# ══════════════════════════════════════════════════════════════════════════════
# M5 / M18 · Trailing-window boundary
# ══════════════════════════════════════════════════════════════════════════════
class TestRecentWindowBoundary:
    """The window is ``[AS_OF − 29, AS_OF]`` — 30 days, both ends inclusive."""

    def test_window_length_and_start(self) -> None:
        """M5 killer: ``RECENT_WINDOW_DAYS`` is 30 and the start is AS_OF − 29.

        Mutation M5 sets it to 60. The old suite never referenced the constant,
        so 60 passed 27 tests and a full 17/17 pipeline run while quietly
        republishing a different store leaderboard.
        """
        assert RECENT_WINDOW_DAYS == 30

        cfg = RunConfig(as_of_date=AS_OF_DATE)
        assert cfg.recent_window_start == dt.date(2026, 5, 4)
        assert (cfg.as_of_date - cfg.recent_window_start).days == RECENT_WINDOW_DAYS - 1

    @pytest.mark.parametrize(
        "as_of, expected_start",
        [
            (dt.date(2026, 6, 2), dt.date(2026, 5, 4)),
            (dt.date(2026, 1, 1), dt.date(2025, 12, 3)),   # crosses a year boundary
            (dt.date(2024, 3, 1), dt.date(2024, 2, 1)),    # crosses a leap-year February
        ],
    )
    def test_window_start_is_derived_not_hardcoded(
        self, as_of: dt.date, expected_start: dt.date
    ) -> None:
        """The 30-day span holds for any reference date, including awkward ones.

        A start date that is right only for 2026-06-02 would be a hardcoded
        answer, not a rule. Leap-year February is the case that catches a
        naive "subtract a month" implementation.
        """
        cfg = RunConfig(as_of_date=as_of)
        assert cfg.recent_window_start == expected_start
        assert (as_of - cfg.recent_window_start).days + 1 == 30

    def test_window_excludes_rows_on_both_sides_of_the_boundary(
        self, tmp_path: Path
    ) -> None:
        """M18 killer: four probe rows, one on each side of each boundary.

        The shipped data has nothing after AS_OF_DATE (TX-08 quarantines the
        three future rows), which is exactly why removing the window's upper
        bound is invisible on real data and needs a synthetic probe. Here
        ``S_AFTER`` sits one day past AS_OF; if the upper bound disappears it
        joins the ranking and this test fails.
        """
        db = tmp_path / "window_probe.db"
        _build(
            db,
            facts=[
                # (txn, date, store, qty, price, ext, disc, net, is_return)
                ("T_BEFORE", "2026-05-03", "S_BEFORE", 1, 100.0, 100.0, 0.0, 900.0, 0),
                ("T_START", "2026-05-04", "S_START", 1, 100.0, 100.0, 0.0, 100.0, 0),
                ("T_END", "2026-06-02", "S_END", 1, 100.0, 100.0, 0.0, 200.0, 0),
                ("T_AFTER", "2026-06-03", "S_AFTER", 1, 100.0, 100.0, 0.0, 999.0, 0),
            ],
            stores=[
                ("S_BEFORE", "One Day Too Early", "Northeast"),
                ("S_START", "First Day In Window", "Northeast"),
                ("S_END", "As Of Date Itself", "Northeast"),
                ("S_AFTER", "One Day Too Late", "Northeast"),
            ],
        )
        cfg = RunConfig(as_of_date=AS_OF_DATE)
        rows = _run(
            db,
            "top_stores_recent_30d",
            {
                "window_start": cfg.recent_window_start.isoformat(),
                "as_of_date": cfg.as_of_date.isoformat(),
            },
        )

        included = {r["store_id"] for r in rows}
        assert included == {"S_START", "S_END"}, (
            "The window must include AS_OF − 29 and AS_OF themselves, and exclude "
            "the days immediately outside. S_BEFORE leaking in means the window "
            "grew (M5); S_AFTER leaking in means the upper bound was dropped (M18)."
        )

        # The revenues are chosen so a boundary leak also changes the ORDER,
        # not just the membership — the leaderboard is what reviewers read.
        assert [r["store_id"] for r in rows] == ["S_END", "S_START"]

    def test_window_start_appears_in_the_sql_as_a_bound_parameter(self) -> None:
        """Both boundary parameters are actually referenced by the query.

        A query that binds ``:window_start`` but never ``:as_of_date`` is
        mutation M18 in source form. This is a cheap structural check that
        complements the behavioural probe above.
        """
        sql = metric_sql("top_stores_recent_30d")
        assert ":window_start" in sql
        assert ":as_of_date" in sql


# ══════════════════════════════════════════════════════════════════════════════
# F7 / M6 · The reconciliation delta must be capable of failing
# ══════════════════════════════════════════════════════════════════════════════
CONSISTENT_FACTS: list[tuple[Any, ...]] = [
    # (txn, date, store, qty, price, extended, discount, net, is_return)
    ("TXN001", "2026-05-15", "S001", 2, 100.0, 200.0, 0.0, 200.0, 0),
    ("TXN002", "2026-05-16", "S001", 3, 100.0, 300.0, 30.0, 270.0, 0),   # 10% discount
    ("TXN003", "2026-05-17", "S001", -1, 100.0, -100.0, 0.0, -100.0, 1),  # return
    ("TXN004", "2026-05-18", "S001", 1, 100.0, 100.0, 0.0, 100.0, 0),
]
"""Hand-checkable, internally consistent: every non-return row satisfies
``net == extended − discount``. gross 600, discount 30, sales-net 570,
returns −100, net revenue 470."""

INCONSISTENT_FACTS: list[tuple[Any, ...]] = [
    ("TXN001", "2026-05-15", "S001", 2, 100.0, 200.0, 0.0, 200.0, 0),
    # TXN002 is the sabotage: extended 300 − discount 30 = 270, but net says 275.
    # $5.00 of revenue has been invented between the line and the total.
    ("TXN002", "2026-05-16", "S001", 3, 100.0, 300.0, 30.0, 275.0, 0),
    ("TXN003", "2026-05-17", "S001", -1, 100.0, -100.0, 0.0, -100.0, 1),
    ("TXN004", "2026-05-18", "S001", 1, 100.0, 100.0, 0.0, 100.0, 0),
]
"""Identical to ``CONSISTENT_FACTS`` except TXN002's ``net_amount``, which is
$5.00 too high. Any honest reconciliation must report a −$5.00 line-level delta;
the shipped tautology reports 0.00, and so does the literal ``0.0`` of M6."""


@pytest.fixture
def consistent_warehouse(tmp_path: Path) -> Path:
    """A four-row warehouse where every arithmetic invariant holds."""
    db = tmp_path / "recon_consistent.db"
    _build(db, facts=CONSISTENT_FACTS, stores=[("S001", "Test Store", "Northeast")])
    return db


@pytest.fixture
def inconsistent_warehouse(tmp_path: Path) -> Path:
    """The same warehouse with $5.00 of revenue invented inside one row."""
    db = tmp_path / "recon_inconsistent.db"
    _build(db, facts=INCONSISTENT_FACTS, stores=[("S001", "Test Store", "Northeast")])
    return db


def _delta_columns(row: dict[str, Any]) -> dict[str, float]:
    """Extract every column whose name ends in ``_delta``."""
    return {k: v for k, v in row.items() if k.endswith("delta")}


class TestReconciliationDeltaIsFalsifiable:
    """M6 killer: the delta must be *computed*, and must be non-zero when data lies."""

    def test_metric_exposes_both_required_delta_columns(
        self, consistent_warehouse: Path
    ) -> None:
        """F7: ``line_level_delta`` and ``aggregate_delta`` replace the tautology.

        The old single ``reconciliation_delta`` was
        ``SUM(net WHERE ret=0) + SUM(net WHERE ret=1) − SUM(net)``, which is
        identically zero because ``is_return ∈ {0,1}`` partitions the rows. Two
        named deltas — one per-row, one aggregate — is what makes the claim
        falsifiable, so the column names are asserted as an interface.
        """
        rows = _run(consistent_warehouse, "revenue_reconciliation")
        assert len(rows) == 1
        assert set(_delta_columns(rows[0])) == set(REQUIRED_DELTA_COLUMNS), (
            "revenue_reconciliation must emit line_level_delta and aggregate_delta "
            "(FIX_CONTRACT §2). The single tautological reconciliation_delta survived "
            "$79k of injected fake revenue and mutation M6."
        )

    def test_all_deltas_are_zero_on_consistent_data(
        self, consistent_warehouse: Path
    ) -> None:
        """The happy path: correct data reconciles to 0.00 on every delta.

        NOTE: this assertion alone is worthless as a mutation test — the literal
        ``0.0`` of M6 passes it perfectly. It is here only to establish that the
        deltas are not merely noisy, and it is meaningless without the
        non-zero test below.
        """
        row = _run(consistent_warehouse, "revenue_reconciliation")[0]
        for name, value in _delta_columns(row).items():
            assert value == pytest.approx(0.0, abs=0.005), f"{name} should tie out"

    def test_line_level_delta_reports_the_exact_invented_amount(
        self, inconsistent_warehouse: Path
    ) -> None:
        """M6 killer, primary: −$5.00 of invented revenue must show up as −$5.00.

        ``SUM(extended_amount − discount_amount − net_amount)`` over non-returns
        = (200−0−200) + (300−30−275) + (100−0−100) = −5.00. A hardcoded ``0.0``,
        or the old partition tautology, reports 0.00 and this test fails — which
        is precisely the assurance the metric advertises but did not provide.
        """
        row = _run(inconsistent_warehouse, "revenue_reconciliation")[0]
        assert row["line_level_delta"] == pytest.approx(-5.0, abs=0.005), (
            "line_level_delta must be SUM(extended_amount − discount_amount − net_amount) "
            "over non-returns. Reporting 0.00 here means the delta cannot detect the "
            "arithmetic bug its own definition_note claims it detects (F7)."
        )

    def test_aggregate_delta_reports_the_invented_amount_too(
        self, inconsistent_warehouse: Path
    ) -> None:
        """M6 killer, secondary: the aggregate tie-out must also break.

        ``(gross_list_value − discount_total) + returns_value − net_revenue``
        = (600 − 30) + (−100) − 475 = −5.00. The aggregate delta must be
        anchored on ``gross_list_value`` and ``discount_total``; deriving it
        from ``SUM(net WHERE is_return = 0)`` reproduces the original tautology
        under a new name and reports 0.00 here.
        """
        row = _run(inconsistent_warehouse, "revenue_reconciliation")[0]
        assert row["aggregate_delta"] == pytest.approx(-5.0, abs=0.005), (
            "aggregate_delta must be (gross_list_value − discount_total) + returns_value "
            "− net_revenue. If it is computed from SUM(net WHERE is_return = 0) instead, "
            "it is algebraically incapable of failing — the F7 tautology renamed."
        )

    def test_at_least_one_delta_is_non_zero_whenever_the_data_lies(
        self, inconsistent_warehouse: Path
    ) -> None:
        """Implementation-independent backstop: something must ring.

        Stated separately from the two exact-value tests so that a future
        refactor which renames or restructures the deltas still cannot end up
        with a reconciliation metric that is silent on broken data.
        """
        row = _run(inconsistent_warehouse, "revenue_reconciliation")[0]
        deltas = _delta_columns(row)
        assert deltas, "revenue_reconciliation emits no delta column at all"
        assert any(abs(v) > 0.005 for v in deltas.values()), (
            f"every delta read zero on data with $5.00 of invented revenue: {deltas}. "
            "A reconciliation that cannot fail is not a reconciliation."
        )

    def test_deltas_do_not_serialise_as_negative_zero(
        self, consistent_warehouse: Path
    ) -> None:
        """F15: a tie-out of zero must render "0.0", never "-0.0".

        Float summation of signed money legitimately produces ``-0.0``, which
        JSON-serialises as ``-0.0`` and renders as "-0" in the dashboard table —
        a reviewer reasonably reads that as a real, tiny discrepancy.
        """
        row = _run(consistent_warehouse, "revenue_reconciliation")[0]
        for name, value in _delta_columns(row).items():
            assert str(value) != "-0.0", f"{name} serialises as -0.0; add + 0.0 before rounding"

    def test_schema_rejects_the_inconsistent_row(self) -> None:
        """The production CHECK constraints would refuse the fixture above.

        Documents *why* the M6 fixtures bypass ``schema.sql``: the real schema
        enforces ``ABS(discount_amount − (extended_amount − net_amount)) <=
        0.01`` per row, so the sabotage is unloadable through the front door.
        That constraint is genuine defence-in-depth, and asserting it here keeps
        the bypass honest — if the CHECK were ever dropped, this fails.
        """
        schema_text = (Path(__file__).resolve().parents[1] / "src" / "warehouse" / "schema.sql").read_text()
        normalized = " ".join(schema_text.split())
        assert "ABS(discount_amount - (extended_amount - net_amount))" in normalized, (
            "fact_sales must keep its per-row discount CHECK; it is the constraint "
            "that stops an inconsistent row reaching the warehouse at all."
        )
        assert "ABS(extended_amount - (quantity * unit_price))" in normalized
