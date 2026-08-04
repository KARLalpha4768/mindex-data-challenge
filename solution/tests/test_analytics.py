"""Tests for the analytics layer against controlled SQLite fixtures.

Each test builds a small, deterministic warehouse with known data, executes
one or more analytics queries, and asserts exact results. This verifies that
the SQL in queries.py answers the right question correctly — not just that
the queries execute without error.

Coverage:
  - Return rate calculation: a store with 1 return out of 4 transactions
  - Net revenue calculation: sales + returns = correct net
  - Top customers: GUEST excluded from lifetime leaderboard
  - Revenue reconciliation: gross, discount, returns and net tie out

Deliberately NOT covered here:
  - The reconciliation *deltas*' ability to fail  -> ``test_metric_contracts.py``
  - The trailing-window boundary                  -> ``test_metric_contracts.py``
  - Any real-data value                           -> ``test_golden_end_to_end.py``

WHY SQL IS RESOLVED THROUGH ``METRIC_REGISTRY`` (finding F11)
-------------------------------------------------------------
This module used to import the query constants by their Python names. The
*metric id* is the binding interface — contract §6 names all six, they key
``analytics.json``, and the dashboard reads them — while the Python constant
name is an implementation detail. Looking queries up by id means a rename of
the id cannot pass unnoticed, and an internal constant rename does not churn
the suite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.config import AS_OF_DATE, RETURN_RATE_ALERT_THRESHOLD, RunConfig

from .conftest import metric_sql


def _build_test_warehouse(db_path: Path) -> None:
    """Create a minimal warehouse with known, hand-calculated data.

    The fixture contains:
      - 1 store (S001, Northeast)
      - 1 product (P001, Electronics, $100)
      - 1 non-guest customer (CUST001) + 1 GUEST member
      - 4 fact rows:
          TXN001: sale, qty=2, price=100, ext=200, disc=0, net=200
          TXN002: sale, qty=3, price=100, ext=300, disc=30, net=270 (10% discount)
          TXN003: return, qty=-1, price=100, ext=-100, disc=0, net=-100
          TXN004: guest sale, qty=1, price=100, ext=100, disc=0, net=100
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # dim_date — covering 2026-05-01 to 2026-06-02
    cur.execute("""
        CREATE TABLE dim_date (
            date_key INTEGER PRIMARY KEY,
            full_date TEXT NOT NULL UNIQUE,
            year INTEGER, quarter INTEGER, month INTEGER,
            year_month TEXT, month_name TEXT,
            day_of_month INTEGER, day_of_week INTEGER, is_weekend INTEGER
        )
    """)
    # Insert just the dates we need
    dates = [
        (20260515, "2026-05-15", 2026, 2, 5, "2026-05", "May", 15, 4, 0),
        (20260516, "2026-05-16", 2026, 2, 5, "2026-05", "May", 16, 5, 0),
        (20260517, "2026-05-17", 2026, 2, 5, "2026-05", "May", 17, 6, 1),
        (20260518, "2026-05-18", 2026, 2, 5, "2026-05", "May", 18, 0, 1),
    ]
    cur.executemany(
        "INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?,?)", dates
    )

    # dim_store
    cur.execute("""
        CREATE TABLE dim_store (
            store_key INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id TEXT UNIQUE NOT NULL, store_name TEXT, city TEXT,
            state TEXT, zip_code TEXT, zip_is_suspect INTEGER DEFAULT 0,
            region TEXT NOT NULL, region_is_imputed INTEGER DEFAULT 0,
            opened_date TEXT
        )
    """)
    cur.execute(
        "INSERT INTO dim_store (store_id, store_name, city, state, zip_code, region) "
        "VALUES ('S001', 'Test Store', 'Rochester', 'NY', '14604', 'Northeast')"
    )

    # dim_product
    cur.execute("""
        CREATE TABLE dim_product (
            product_key INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT UNIQUE NOT NULL, product_name TEXT,
            category TEXT NOT NULL, category_is_imputed INTEGER DEFAULT 0,
            list_unit_price REAL NOT NULL, price_is_imputed INTEGER DEFAULT 0,
            price_conflict INTEGER DEFAULT 0, supplier_id TEXT
        )
    """)
    cur.execute(
        "INSERT INTO dim_product (product_id, product_name, category, list_unit_price) "
        "VALUES ('P001', 'Test Product', 'Electronics', 100.00)"
    )

    # dim_customer
    cur.execute("""
        CREATE TABLE dim_customer (
            customer_key INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT UNIQUE NOT NULL, is_guest INTEGER NOT NULL
        )
    """)
    cur.execute("INSERT INTO dim_customer (customer_id, is_guest) VALUES ('CUST001', 0)")
    cur.execute("INSERT INTO dim_customer (customer_id, is_guest) VALUES ('GUEST', 1)")

    # fact_sales — 4 rows with hand-calculated measures
    cur.execute("""
        CREATE TABLE fact_sales (
            sales_key INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE NOT NULL,
            date_key INTEGER NOT NULL, store_key INTEGER NOT NULL,
            product_key INTEGER NOT NULL, customer_key INTEGER NOT NULL,
            quantity INTEGER NOT NULL, unit_price REAL NOT NULL,
            extended_amount REAL NOT NULL, discount_amount REAL NOT NULL,
            net_amount REAL NOT NULL, is_return INTEGER NOT NULL
        )
    """)
    # Resolve keys
    store_key = cur.execute("SELECT store_key FROM dim_store WHERE store_id='S001'").fetchone()[0]
    product_key = cur.execute("SELECT product_key FROM dim_product WHERE product_id='P001'").fetchone()[0]
    cust_key = cur.execute("SELECT customer_key FROM dim_customer WHERE customer_id='CUST001'").fetchone()[0]
    guest_key = cur.execute("SELECT customer_key FROM dim_customer WHERE customer_id='GUEST'").fetchone()[0]

    facts = [
        # TXN001: clean sale, qty=2, price=100, no discount
        ("TXN001", 20260515, store_key, product_key, cust_key, 2, 100.0, 200.0, 0.0, 200.0, 0),
        # TXN002: discounted sale, qty=3, price=100, 10% discount (net=270)
        ("TXN002", 20260516, store_key, product_key, cust_key, 3, 100.0, 300.0, 30.0, 270.0, 0),
        # TXN003: return, qty=-1, price=100 (net=-100)
        ("TXN003", 20260517, store_key, product_key, cust_key, -1, 100.0, -100.0, 0.0, -100.0, 1),
        # TXN004: guest sale, qty=1, price=100 (net=100)
        ("TXN004", 20260518, store_key, product_key, guest_key, 1, 100.0, 100.0, 0.0, 100.0, 0),
    ]
    cur.executemany(
        "INSERT INTO fact_sales "
        "(transaction_id, date_key, store_key, product_key, customer_key, "
        "quantity, unit_price, extended_amount, discount_amount, net_amount, is_return) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        facts,
    )

    conn.commit()
    conn.close()


@pytest.fixture
def test_warehouse(tmp_path: Path) -> Path:
    """A fully loaded test warehouse with 4 known fact rows."""
    db_path = tmp_path / "test_warehouse.db"
    _build_test_warehouse(db_path)
    return db_path


class TestReturnRate:
    """Verify return rate calculation against hand-calculated expected values."""

    def test_return_rate_exact(self, test_warehouse: Path) -> None:
        """S001 has 4 transactions: 3 sales + 1 return.
        Txn-based return rate = 1/4 = 25.00%.
        Unit-based: sold = 2+3+1 = 6 units, returned = 1 unit → 1/(6+1) ≈ 14.29%."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row
        params = {"return_rate_threshold": RETURN_RATE_ALERT_THRESHOLD}

        rows = [dict(r) for r in conn.execute(metric_sql("return_rate_by_store"), params).fetchall()]
        conn.close()

        assert len(rows) == 1, "Should have exactly 1 store"
        store = rows[0]

        assert store["store_id"] == "S001"
        assert store["total_transactions"] == 4
        assert store["return_transactions"] == 1

        # Transaction-based rate: 1/4 = 25%
        assert store["txn_return_rate_pct"] == 25.0

        # Unit-based: returned=1, total=|2|+|3|+|-1|+|1| = 7
        # rate = 1/7 ≈ 14.29%
        assert abs(store["unit_return_rate_pct"] - 14.29) < 0.1

        # 14.29% > 10% threshold → flag should be 1
        assert store["exceeds_threshold"] == 1


class TestNetRevenue:
    """Verify net revenue includes returns as subtractions."""

    def test_net_revenue_with_returns(self, test_warehouse: Path) -> None:
        """Net revenue = 200 + 270 + (-100) + 100 = 470.00.

        STRENGTHENED: the window parameters now come from ``RunConfig`` rather
        than the hand-written literal ``"2026-05-03"`` this test used to pass.
        That literal was one day wider than the real window and, because all
        four fixture dates sit comfortably inside either reading, the test was
        silently exercising a window the pipeline never uses. The boundary
        itself is tested behaviourally in ``test_metric_contracts.py``.
        """
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row
        cfg = RunConfig(as_of_date=AS_OF_DATE)
        params = {
            "as_of_date": cfg.as_of_date.isoformat(),
            "window_start": cfg.recent_window_start.isoformat(),
        }
        assert params == {"as_of_date": "2026-06-02", "window_start": "2026-05-04"}

        rows = [
            dict(r)
            for r in conn.execute(metric_sql("top_stores_recent_30d"), params).fetchall()
        ]
        conn.close()

        assert len(rows) == 1
        assert rows[0]["net_revenue"] == 470.0
        assert rows[0]["transaction_count"] == 4
        assert rows[0]["return_count"] == 1, "returns are netted, not filtered out"


class TestTopCustomersExcludesGuest:
    """Verify GUEST is excluded from lifetime spend leaderboard."""

    def test_guest_excluded(self, test_warehouse: Path) -> None:
        """GUEST has 1 transaction ($100) but must not appear in top customers.

        STRENGTHENED: also pins that the leaderboard has exactly one row and
        that GUEST's $100 is not silently folded into CUST001's total. Excluding
        the *name* while including the *money* would pass the original
        assertions and is the more plausible bug of the two.
        """
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(metric_sql("top_customers_lifetime"), {}).fetchall()]
        conn.close()

        customer_ids = [r["customer_id"] for r in rows]
        assert customer_ids == ["CUST001"], "GUEST must be excluded from the leaderboard entirely"

        cust001 = rows[0]
        # CUST001 has 3 transactions: 200 + 270 + (-100) = 370. GUEST's 100 is
        # not part of it, so 470 here would mean the exclusion is cosmetic.
        assert cust001["lifetime_spend"] == 370.0
        assert cust001["transaction_count"] == 3
        assert cust001["avg_order_value"] == pytest.approx(370.0 / 3, abs=0.005)


class TestRevenueReconciliation:
    """Verify the revenue reconciliation ties out exactly."""

    def test_reconciliation_ties_out(self, test_warehouse: Path) -> None:
        """Gross 600 − discount 30 = 570; + returns (−100) = net 470.

        UPDATED (F7 / mutation M6): the single ``reconciliation_delta`` this
        test used to assert was ``SUM(net WHERE ret=0) + SUM(net WHERE ret=1)
        − SUM(net)`` — identically zero for any data, because ``is_return``
        partitions the rows. Asserting it equalled 0.00 proved nothing, and the
        assertion survived replacing the whole expression with the literal
        ``0.0``. The two replacement deltas are asserted here on *good* data and,
        crucially, on *bad* data in
        ``test_metric_contracts.py::TestReconciliationDeltaIsFalsifiable`` —
        only the second direction shows they can fail.
        """
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(metric_sql("revenue_reconciliation"), {}).fetchall()]
        conn.close()

        assert len(rows) == 1
        recon = rows[0]

        assert recon["gross_list_value"] == 600.0
        assert recon["discount_total"] == 30.0
        assert recon["gross_sales_net_of_discount"] == 570.0
        assert recon["returns_value"] == -100.0
        assert recon["net_revenue"] == 470.0

        assert recon["line_level_delta"] == 0.0, (
            "every non-return row must satisfy net == extended - discount"
        )
        assert recon["aggregate_delta"] == 0.0, (
            "the printed figures must add up: (gross - discount) + returns = net"
        )
        assert "reconciliation_delta" not in recon, (
            "the tautological single delta is replaced by line_level_delta and "
            "aggregate_delta (FIX_CONTRACT §2)"
        )


class TestAvgTxnValueByRegion:
    """Verify average transaction value excludes returns."""

    def test_avg_excludes_returns(self, test_warehouse: Path) -> None:
        """Non-return transactions: TXN001=200, TXN002=270, TXN004=100.
        AVG = (200 + 270 + 100) / 3 = 190.00."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(metric_sql("aov_by_region"), {}).fetchall()]
        conn.close()

        assert len(rows) == 1
        assert rows[0]["region"] == "Northeast"
        assert rows[0]["transaction_count"] == 3  # excludes the 1 return
        assert rows[0]["total_revenue"] == 570.0
        assert abs(rows[0]["avg_transaction_value"] - 190.0) < 0.01

        # Including the −100 return would give 4 txns / 470 / 117.50. Pinning all
        # three numbers means a change to the exclusion cannot pass by moving the
        # error into a column this test does not read (mutation M8).
        assert rows[0]["total_revenue"] != 470.0
