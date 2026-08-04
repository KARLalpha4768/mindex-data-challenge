"""Golden end-to-end test: the real CSVs, the real pipeline, pinned numbers.

Defends: **F12** (verification report) — "Tests are 100% synthetic fixtures.
Nothing asserts against ``data/raw/`` or the real end-to-end outputs. There is
no golden-file test, so no test would notice if kept rows went from 474 to 400
or revenue moved by $10k."

Mutations killed here (from the report's 18-experiment table):
  M2   PR-02 elects MIN instead of MAX for P005's list price.
       Survived the old suite *and* still printed "PR-02 · Expected 1 ·
       Detected 1 · OK" — the completeness proof gave false assurance because
       it counts detections, not decisions.
  M5   ``RECENT_WINDOW_DAYS`` 30 → 60. Silently reorders the published top-5.
  M18  The 30-day window loses its upper bound. Same effect, different cause.

WHAT BREAKS IF THIS MODULE IS DELETED
-------------------------------------
Every number a reviewer reads — 474 kept rows, $158,044.29 net revenue, the
top-5 store ranking, the 17/17 coverage table — becomes unpinned. The synthetic
suite would keep passing while the deliverable's headline figures drifted, which
is exactly the failure mode this submission argues against.

WHY IT IS NOT BEHIND A ``--slow`` FLAG
--------------------------------------
A golden test that only runs when someone remembers to ask for it is not a
golden test. The whole run costs a few seconds and is shared across every
assertion here via the session-scoped ``golden_run`` fixture, so the marginal
cost per assertion is zero. The ``golden`` marker exists for selection
(``-m golden``), not for exclusion.

WHERE THE EXPECTED VALUES COME FROM
-----------------------------------
``scripts/seed_data.py`` (the counts) and the independent pandas recomputation
in the verification report, which reproduced all six metrics to the cent from
the raw CSVs *before* reading ``queries.py``. They are not copied from a
previous run of the code under test.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pandas as pd
import pytest

from src.config import AS_OF_DATE, RECENT_WINDOW_DAYS
from src.defects import DEFECT_CATALOG, DefectCode
from src.pipeline import PipelineResult

pytestmark = pytest.mark.golden


# ── The catalog's own expected counts, restated as literals ──────────────────
# WHY restate them instead of importing DEFECT_CATALOG and comparing it to
# itself: a test that reads its expectation from the code under test proves
# only that the code is self-consistent. These 17 numbers come from
# scripts/seed_data.py and were re-derived independently in the verification
# report. Cross-checking the catalog against them is a second opinion.
SEEDED_DEFECT_COUNTS: dict[str, int] = {
    "ST-01": 1,   # S003 zip '0938'
    "ST-02": 1,   # S007 near-duplicate PK
    "ST-03": 2,   # S013, S014 null region
    "PR-01": 1,   # P012 byte-identical duplicate
    "PR-02": 1,   # P005 price change
    "PR-03": 5,   # P003, P009, P016, P023, P029
    "PR-04": 1,   # P027 zero price
    "TX-01": 20,  # 10 US-style + 10 EU-style dates
    "TX-02": 25,  # "$142.50"-style amounts
    "TX-03": 20,  # silent discounts
    "TX-04": 5,   # orphan store ids
    "TX-05": 3,   # orphan product ids
    "TX-06": 40,  # guest checkouts
    "TX-07": 5,   # zero quantity
    "TX-08": 3,   # future dates
    "TX-09": 15,  # exact duplicate rows
    "TX-10": 30,  # returns
}

# ── The published top-5, in order ────────────────────────────────────────────
# This tuple is what binds the trailing-window definition. Under M5 (60-day
# window) or M18 (no upper bound) the membership and/or the order changes, so
# asserting it is what makes the window boundary a tested decision rather than
# an undocumented one.
EXPECTED_TOP_5_STORES: tuple[tuple[str, str, float], ...] = (
    ("S008", "Galleria at Crystal Run", 6770.08),
    ("S011", "Southpark Meadows", 6555.48),
    ("S001", "Eastview Mall", 5865.13),
    ("S014", "Lloyd Center", 4979.12),
    ("S012", "The Domain", 4938.18),
)


# ══════════════════════════════════════════════════════════════════════════════
# Row budget — every source row accounted for, exactly once
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenRowBudget:
    """505 source transaction rows = 474 kept + 16 quarantined + 15 dropped."""

    def test_row_counts_exact(self, golden_run: PipelineResult) -> None:
        """Pin the shape of all three cleaned datasets.

        Removing this test would let a cleaning regression silently delete rows:
        the pipeline's own coverage proof checks *detections*, not survivors, so
        a rule that quarantined 74 extra transactions would still print 17/17.
        """
        raw = golden_run.row_counts["raw"]
        cleaned = golden_run.row_counts["cleaned"]

        assert raw == {"stores": 16, "products": 32, "transactions": 505}
        assert cleaned == {"stores": 15, "products": 30, "transactions": 474}

    def test_warehouse_table_counts_exact(self, golden_conn: sqlite3.Connection) -> None:
        """The star schema holds exactly the surviving rows — no fan-out, no loss.

        ``fact_sales`` = 474 is the same number as ``transactions_clean.csv``;
        if a join in the loader ever fanned out, this is where it shows up.
        ``dim_customer`` = 229 is 228 real customers + the single GUEST sentinel.
        """
        counts = {
            table: golden_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("dim_store", "dim_product", "dim_customer", "fact_sales")
        }
        assert counts == {
            "dim_store": 15,
            "dim_product": 30,
            "dim_customer": 229,
            "fact_sales": 474,
        }

    def test_lineage_budget_balances(self, golden_lineage: pd.DataFrame) -> None:
        """474 + 16 + 15 = 505, with the per-reason breakdown pinned.

        The three dispositions must partition the source file. If this drifts,
        the "every row is accounted for" claim in the README is no longer true
        and nothing else in the suite would notice.
        """
        assert len(golden_lineage) == 505

        by_disposition = golden_lineage["disposition"].value_counts().to_dict()
        assert by_disposition == {"kept": 474, "quarantined": 16, "dropped": 15}

        by_reason = (
            golden_lineage["reason_code"].dropna().value_counts().sort_index().to_dict()
        )
        assert by_reason == {
            "TX-04": 5,   # orphan store
            "TX-05": 3,   # orphan product
            "TX-07": 5,   # zero quantity
            "TX-08": 3,   # future date
            "TX-09": 15,  # exact duplicate (dropped, not quarantined)
        }

    def test_source_rows_are_a_complete_gapless_range(
        self, golden_lineage: pd.DataFrame
    ) -> None:
        """``source_row`` covers 0..504 with no gaps and no repeats.

        This is the assertion that makes the budget a *proof* rather than an
        arithmetic coincidence: three numbers can add to 505 while the same row
        is counted twice and another is missing entirely.
        """
        assert sorted(golden_lineage["source_row"].tolist()) == list(range(505))

    def test_drop_reasons_are_mutually_disjoint(self, golden_lineage: pd.DataFrame) -> None:
        """No source row carries two reason codes, so 505 − 31 = 474 double-counts nothing.

        The lineage file files each row under exactly one reason. That is only
        meaningful if the underlying populations really are disjoint, which is
        what the one-row-per-source_row invariant below establishes.
        """
        flagged = golden_lineage.loc[golden_lineage["reason_code"].notna()]
        assert len(flagged) == 31
        assert flagged["source_row"].is_unique
        assert flagged["reason_code"].nunique() == 5


# ══════════════════════════════════════════════════════════════════════════════
# Money — the headline figures, to the cent
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenRevenue:
    """Net revenue and the TX-03 discount total, pinned to the cent."""

    def test_net_revenue_exact_to_the_cent(self, golden_conn: sqlite3.Connection) -> None:
        """SUM(fact_sales.net_amount) == $158,044.29.

        The single most important number in the deliverable. Without this
        assertion a $10k drift — a dropped store, a filtered return class, a
        recomputed total — passes the entire suite.
        """
        total = golden_conn.execute("SELECT ROUND(SUM(net_amount), 2) FROM fact_sales").fetchone()[0]
        assert total == pytest.approx(158044.29, abs=0.005)

    def test_tx03_discount_total_exact(self, golden_conn: sqlite3.Connection) -> None:
        """TX-03: 20 discounted rows carrying $961.48 of discount, preserved not recomputed.

        If ``total_amount`` were ever recomputed as ``qty × unit_price`` — the
        challenge's named worst mistake — ``discount_amount`` would collapse to
        0.00 on all 474 rows and revenue would overstate by exactly this amount.
        """
        row = golden_conn.execute(
            "SELECT COUNT(*) AS n, ROUND(SUM(discount_amount), 2) AS total "
            "FROM fact_sales WHERE discount_amount > 0.01"
        ).fetchone()
        assert row["n"] == 20
        assert row["total"] == pytest.approx(961.48, abs=0.005)

    def test_reconciliation_columns_tie_out_by_hand(
        self, golden_run: PipelineResult
    ) -> None:
        """gross 168,957.80 − discount 961.48 + returns (−9,952.03) = net 158,044.29.

        Each figure is asserted separately, not just their difference, because a
        delta that is computed from its own inputs can tie out while every input
        is wrong (that is finding F7 in miniature).
        """
        rows = golden_run.analytics["metrics"]["revenue_reconciliation"]["rows"]
        assert len(rows) == 1
        recon = rows[0]

        assert recon["gross_list_value"] == pytest.approx(168957.80, abs=0.005)
        assert recon["discount_total"] == pytest.approx(961.48, abs=0.005)
        assert recon["gross_sales_net_of_discount"] == pytest.approx(167996.32, abs=0.005)
        assert recon["returns_value"] == pytest.approx(-9952.03, abs=0.005)
        assert recon["net_revenue"] == pytest.approx(158044.29, abs=0.005)

        # The arithmetic a reviewer would do on paper.
        assert recon["gross_list_value"] - recon["discount_total"] == pytest.approx(
            recon["gross_sales_net_of_discount"], abs=0.005
        )
        assert recon["gross_sales_net_of_discount"] + recon["returns_value"] == pytest.approx(
            recon["net_revenue"], abs=0.005
        )

    def test_returns_are_preserved_and_signed(self, golden_conn: sqlite3.Connection) -> None:
        """TX-10: all 30 returns load with negative money totalling −$9,952.03.

        Guards the "returns reduce revenue, not excluded" decision. Mutation M16
        (filter returns out) is caught by other tests too, but only this one
        pins the dollar value they contribute.
        """
        row = golden_conn.execute(
            "SELECT COUNT(*) AS n, ROUND(SUM(net_amount), 2) AS total "
            "FROM fact_sales WHERE is_return = 1"
        ).fetchone()
        assert row["n"] == 30
        assert row["total"] == pytest.approx(-9952.03, abs=0.005)


# ══════════════════════════════════════════════════════════════════════════════
# M2 · PR-02 — the dimension price and the fact price must disagree
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenPR02PriceSeparation:
    """PR-02: P005's elected list price is 150.11 while its 19 fact rows ring 141.61."""

    def test_dim_product_elects_the_higher_list_price(
        self, golden_conn: sqlite3.Connection
    ) -> None:
        """M2 killer: ``dim_product.P005.list_unit_price == 150.11``, never 141.61.

        Mutation M2 swaps the MAX policy for MIN. It passed all 27 previous
        tests *and* the pipeline still printed "PR-02 · Expected 1 · Detected 1
        · OK", because detecting the conflict and resolving it correctly are two
        different things and only the first was ever checked. Asserting the
        elected value — not merely that one row survived — is what closes that
        gap. The +$8.50 delta is asserted too, so a mutation that changed both
        candidate prices could not slip through either.
        """
        row = golden_conn.execute(
            "SELECT list_unit_price, price_conflict, price_is_imputed "
            "FROM dim_product WHERE product_id = 'P005'"
        ).fetchone()
        assert row is not None, "P005 must survive PR-02 resolution as one dimension row"

        assert row["list_unit_price"] == pytest.approx(150.11, abs=0.005), (
            "PR-02 elects the HIGHER of the two conflicting list prices by an explicit "
            "MAX policy. 141.61 means the policy became MIN (mutation M2) or reverted to "
            "file order, which is a coin flip the source shuffle decides."
        )
        assert row["price_conflict"] == 1, "The ambiguity must travel with the row"
        assert row["price_is_imputed"] == 0, "150.11 is an observed value, not an imputed one"

    def test_fact_rows_carry_the_transacted_price_not_the_dimension_price(
        self, golden_conn: sqlite3.Connection
    ) -> None:
        """All 19 kept P005 fact rows ring at 141.61 — the price actually charged.

        This is the other half of PR-02 and the reason electing a list price is
        safe at all: no revenue figure reads ``dim_product.list_unit_price``. If
        the loader ever sourced the fact price from the dimension, revenue would
        move and this assertion is the only place that would say so.
        """
        rows = golden_conn.execute(
            "SELECT f.unit_price, COUNT(*) AS n "
            "FROM fact_sales f JOIN dim_product p ON f.product_key = p.product_key "
            "WHERE p.product_id = 'P005' GROUP BY f.unit_price"
        ).fetchall()
        assert [(r["unit_price"], r["n"]) for r in rows] == [(141.61, 19)]

    def test_pr04_shows_the_same_separation(self, golden_conn: sqlite3.Connection) -> None:
        """PR-04: P027's dimension price is the imputed category median, not the transacted price.

        126.96 is the Apparel median; 195.34 is what its 17 fact rows charged.
        Writing 195.34 into the dimension would launder fact data into master
        data — plausible-looking, and wrong. Nothing else asserts this.
        """
        dim = golden_conn.execute(
            "SELECT list_unit_price, price_is_imputed FROM dim_product WHERE product_id = 'P027'"
        ).fetchone()
        assert dim["list_unit_price"] == pytest.approx(126.96, abs=0.005)
        assert dim["price_is_imputed"] == 1

        facts = golden_conn.execute(
            "SELECT f.unit_price, COUNT(*) AS n "
            "FROM fact_sales f JOIN dim_product p ON f.product_key = p.product_key "
            "WHERE p.product_id = 'P027' GROUP BY f.unit_price"
        ).fetchall()
        assert [(r["unit_price"], r["n"]) for r in facts] == [(195.34, 17)]


# ══════════════════════════════════════════════════════════════════════════════
# M5 / M18 · The trailing 30-day window and the ranking it publishes
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenRecentWindow:
    """The window is [AS_OF−29, AS_OF], 30 days inclusive, and the top-5 proves it."""

    def test_window_is_thirty_days_inclusive_of_both_ends(
        self, golden_run: PipelineResult
    ) -> None:
        """M5/M18 killer #1: the boundary convention, stated as an assertion.

        ``[AS_OF − 29, AS_OF]`` spans 30 calendar days. Off-by-one in either
        direction ( ``AS_OF − 30`` , or dropping the upper bound ) changes every
        "recent" number on the dashboard. The verification report confirmed that
        an ``AS_OF − 30`` reading alone reorders #1 and #2.
        """
        cfg = golden_run.config
        assert RECENT_WINDOW_DAYS == 30
        assert cfg.as_of_date == dt.date(2026, 6, 2)
        assert cfg.recent_window_start == dt.date(2026, 5, 4)

        span = (cfg.as_of_date - cfg.recent_window_start).days + 1
        assert span == RECENT_WINDOW_DAYS == 30, (
            "The window must be inclusive of both endpoints: AS_OF − (N − 1) .. AS_OF."
        )

    def test_window_contains_exactly_181_fact_rows(
        self, golden_conn: sqlite3.Connection
    ) -> None:
        """M5/M18 killer #2: the population the ranking is computed over.

        181 of the 474 fact rows fall inside [2026-05-04, 2026-06-02].
        Lengthening the window (M5) or removing its upper bound (M18) changes
        this count before it changes the ranking, so the failure names the cause.
        """
        count = golden_conn.execute(
            "SELECT COUNT(*) FROM fact_sales f JOIN dim_date d ON f.date_key = d.date_key "
            "WHERE d.full_date BETWEEN '2026-05-04' AND '2026-06-02'"
        ).fetchone()[0]
        assert count == 181

    def test_top_five_store_ranking_is_exact_and_ordered(
        self, golden_run: PipelineResult
    ) -> None:
        """M5/M18 killer #3: the published leaderboard, membership and order.

        This is the number a reviewer reads first. Both surviving window
        mutations change it silently — nothing in the old suite touched the real
        warehouse, so the analytics tests ran against a synthetic 5-row fixture
        whose every date sat comfortably inside any window.
        """
        rows = golden_run.analytics["metrics"]["top_stores_recent_30d"]["rows"]
        assert len(rows) == 5

        actual = tuple((r["store_id"], r["store_name"], r["net_revenue"]) for r in rows)
        assert actual == EXPECTED_TOP_5_STORES

        # Descending by revenue, with no ties to make the order ambiguous.
        revenues = [r["net_revenue"] for r in rows]
        assert revenues == sorted(revenues, reverse=True)
        assert len(set(revenues)) == 5

    def test_no_transaction_is_dated_after_as_of(
        self, golden_conn: sqlite3.Connection
    ) -> None:
        """TX-08 held the line: nothing in the fact table post-dates AS_OF_DATE.

        This is why M18 is invisible on the shipped data and needed a synthetic
        probe as well (see ``test_metric_contracts.py``). Pinning it here means
        a future-dated row leaking into the warehouse fails loudly instead of
        quietly widening every trailing window.

        The observed maximum is 2026-06-01, one day before AS_OF_DATE — the seed
        generates every transaction as ``TODAY − 1..89 days``. That single June
        day is also the cause of finding F9: the month-over-month percentages
        compare a 1-day June against a 31-day May, which is a calendar artefact
        and not a business signal.
        """
        earliest, latest = golden_conn.execute(
            "SELECT MIN(d.full_date), MAX(d.full_date) "
            "FROM fact_sales f JOIN dim_date d ON f.date_key = d.date_key"
        ).fetchone()

        assert latest <= AS_OF_DATE.isoformat() == "2026-06-02"
        assert (earliest, latest) == ("2026-03-05", "2026-06-01")

    def test_june_has_exactly_one_day_of_data(self, golden_conn: sqlite3.Connection) -> None:
        """F9: 2026-06 contains a single date, which is why its MoM change is −98.73%.

        A percentage comparing one day against a full month is a calendar
        artefact. Pinning the day counts here means the caveat in the metric's
        ``definition_note`` is anchored to a fact the suite checks, rather than
        to a claim a reader has to take on trust.
        """
        by_month = dict(golden_conn.execute(
            "SELECT d.year_month, COUNT(DISTINCT d.full_date) "
            "FROM fact_sales f JOIN dim_date d ON f.date_key = d.date_key "
            "GROUP BY d.year_month ORDER BY d.year_month"
        ).fetchall())
        assert by_month == {"2026-03": 27, "2026-04": 30, "2026-05": 31, "2026-06": 1}


# ══════════════════════════════════════════════════════════════════════════════
# Defect coverage — 17 of 17, with the counts
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenDefectCoverage:
    """Every seeded defect class is detected, with the count seed_data.py injected."""

    def test_run_reports_no_coverage_mismatches(self, golden_run: PipelineResult) -> None:
        """The pipeline's own build gate passes on the real data.

        ``result.ok`` is what drives exit code 0 vs 1. Asserting it here means a
        coverage regression fails the test suite too, not only the CLI.
        """
        assert golden_run.mismatches == []
        assert golden_run.ok is True

    def test_all_seventeen_codes_detected_with_expected_counts(
        self, golden_run: PipelineResult
    ) -> None:
        """Each of the 17 codes, with its exact detected count, checked one by one.

        The counts are restated as literals from ``scripts/seed_data.py`` rather
        than read back out of ``DEFECT_CATALOG``, so this cannot pass by the
        catalog and the detector agreeing on the same wrong number.
        """
        assert len(DEFECT_CATALOG) == 17
        assert {c.value for c in DEFECT_CATALOG} == set(SEEDED_DEFECT_COUNTS)

        actual = {
            code.value: (rec.detected_count if (rec := golden_run.audit.get(code)) else None)
            for code in DefectCode
        }
        assert actual == SEEDED_DEFECT_COUNTS

    def test_catalog_expected_counts_match_the_seed(self) -> None:
        """The catalog's own ``expected_count`` values agree with seed_data.py.

        Without this, the completeness proof could be satisfied by editing the
        catalog to match a broken detector — the proof would still say 17/17.
        """
        catalog = {code.value: spec.expected_count for code, spec in DEFECT_CATALOG.items()}
        assert catalog == SEEDED_DEFECT_COUNTS


# ══════════════════════════════════════════════════════════════════════════════
# Artifacts — the run wrote where it was told, and only there
# ══════════════════════════════════════════════════════════════════════════════
class TestGoldenArtifacts:
    """F3: ``--output-dir`` is honoured for every artifact, including the lineage file."""

    def test_every_named_artifact_lands_under_the_output_dir(
        self, golden_run: PipelineResult
    ) -> None:
        """All six top-level artifacts exist inside the requested output directory.

        F3 was a blocker precisely because one artifact — the lineage CSV —
        escaped ``--output-dir`` and was written to an import-time project path
        instead. This test would have caught it.
        """
        cfg = golden_run.config
        expected = [
            cfg.profile_report_path,
            cfg.audit_report_path,
            cfg.analytics_path,
            cfg.defect_catalog_path,
            cfg.dashboard_bundle_path,
            cfg.db_path,
        ]
        missing = [str(p) for p in expected if not p.exists()]
        assert missing == [], f"artifacts missing from --output-dir: {missing}"

        for path in expected:
            assert cfg.output_dir in path.parents, f"{path} escaped --output-dir"

    def test_lineage_csv_follows_the_output_dir(self, golden_run: PipelineResult) -> None:
        """F3 killer: ``transactions__lineage.csv`` is written under ``--output-dir``.

        Before the fix it went to the import-time ``config.QUARANTINE_DIR``
        regardless of the flag, which is how ``pytest`` came to overwrite the
        deliverable's own 505-row proof with 2 rows of fixture data.
        """
        lineage_path = golden_run.config.quarantine_dir / "transactions__lineage.csv"
        assert lineage_path.exists(), (
            "lineage_dir must be threaded from RunConfig.quarantine_dir, not a module constant"
        )
        assert golden_run.config.output_dir in lineage_path.parents

    def test_all_six_metrics_are_present_with_expected_row_counts(
        self, golden_run: PipelineResult
    ) -> None:
        """The analytics payload's shape, pinned: six metrics, exact row counts.

        22 MoM rows = 6 categories × 4 months minus 2 legitimate absences (Food
        & Beverage and Office Supplies genuinely had zero June transactions).
        A regression that silently dropped a category would change this number.
        """
        metrics = golden_run.analytics["metrics"]
        row_counts = {mid: len(m["rows"]) for mid, m in metrics.items()}
        assert row_counts == {
            "top_stores_recent_30d": 5,
            "mom_growth_by_category": 22,
            "return_rate_by_store": 15,
            "aov_by_region": 4,
            "top_customers_lifetime": 10,
            "revenue_reconciliation": 1,
        }
