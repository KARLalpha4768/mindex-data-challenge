"""Tests for the analytics layer against controlled SQLite fixtures.

Each test builds a small, deterministic warehouse with known data, executes
one or more analytics queries, and asserts exact results. This verifies that
the SQL in queries.py answers the right question correctly — not just that
the queries execute without error.

Coverage:
  - Return rate calculation: a store with 1 return out of 3 transactions → 33.33%
  - Net revenue calculation: sales + returns = correct net
  - Top customers: GUEST excluded from lifetime leaderboard
  - Revenue reconciliation: discount amounts tie out
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.analytics.queries import (
    RETURN_RATE_BY_STORE,
    TOP_CUSTOMERS_LIFETIME,
    TOP_STORES_RECENT_30D,
    REVENUE_RECONCILIATION,
    AVG_TXN_VALUE_BY_REGION,
)
from src.config import AS_OF_DATE, RETURN_RATE_ALERT_THRESHOLD


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

        rows = [dict(r) for r in conn.execute(RETURN_RATE_BY_STORE, params).fetchall()]
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
        """Net revenue = 200 + 270 + (-100) + 100 = 470.00."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row
        params = {
            "as_of_date": "2026-06-02",
            "window_start": "2026-05-03",  # 30-day window covers all test dates
        }

        rows = [dict(r) for r in conn.execute(TOP_STORES_RECENT_30D, params).fetchall()]
        conn.close()

        assert len(rows) == 1
        assert rows[0]["net_revenue"] == 470.0
        assert rows[0]["transaction_count"] == 4
        assert rows[0]["return_count"] == 1


class TestTopCustomersExcludesGuest:
    """Verify GUEST is excluded from lifetime spend leaderboard."""

    def test_guest_excluded(self, test_warehouse: Path) -> None:
        """GUEST has 1 transaction ($100) but should not appear in top customers.
        Only CUST001 should appear."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(TOP_CUSTOMERS_LIFETIME, {}).fetchall()]
        conn.close()

        customer_ids = [r["customer_id"] for r in rows]
        assert "GUEST" not in customer_ids, "GUEST must be excluded from lifetime leaderboard"
        assert "CUST001" in customer_ids

        cust001 = [r for r in rows if r["customer_id"] == "CUST001"][0]
        # CUST001 has 3 transactions: 200 + 270 + (-100) = 370
        assert cust001["lifetime_spend"] == 370.0
        assert cust001["transaction_count"] == 3


class TestRevenueReconciliation:
    """Verify the revenue reconciliation ties out exactly."""

    def test_reconciliation_ties_out(self, test_warehouse: Path) -> None:
        """Gross list value of sales = 200 + 300 + 100 = 600.
        Discount total = 30.
        Net sales = 200 + 270 + 100 = 570.
        Returns = -100.
        Net revenue = 570 + (-100) = 470.
        Reconciliation delta must be 0.00."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(REVENUE_RECONCILIATION, {}).fetchall()]
        conn.close()

        assert len(rows) == 1
        recon = rows[0]

        assert recon["gross_list_value"] == 600.0
        assert recon["discount_total"] == 30.0
        assert recon["gross_sales_net_of_discount"] == 570.0
        assert recon["returns_value"] == -100.0
        assert recon["net_revenue"] == 470.0
        assert recon["reconciliation_delta"] == 0.0, (
            "Reconciliation must tie: gross - discount + returns = net"
        )


class TestAvgTxnValueByRegion:
    """Verify average transaction value excludes returns."""

    def test_avg_excludes_returns(self, test_warehouse: Path) -> None:
        """Non-return transactions: TXN001=200, TXN002=270, TXN004=100.
        AVG = (200 + 270 + 100) / 3 = 190.00."""
        conn = sqlite3.connect(str(test_warehouse))
        conn.row_factory = sqlite3.Row

        rows = [dict(r) for r in conn.execute(AVG_TXN_VALUE_BY_REGION, {}).fetchall()]
        conn.close()

        assert len(rows) == 1
        assert rows[0]["region"] == "Northeast"
        assert rows[0]["transaction_count"] == 3  # excludes the 1 return
        assert rows[0]["total_revenue"] == 570.0
        assert abs(rows[0]["avg_transaction_value"] - 190.0) < 0.01
