"""Shared pytest fixtures for the Mindex pipeline test suite.

Every fixture that a test needs — controlled DataFrames, temporary databases,
a pre-loaded warehouse, a populated audit log — lives here so individual test
modules stay focused on assertions rather than setup.

Fixture naming convention:
  raw_*         — raw (string-typed) DataFrames matching seed_data.py's output.
  clean_*       — post-cleaning DataFrames, suitable for loading.
  db_*          — paths to SQLite databases.
  warehouse_*   — connections or paths to fully loaded warehouses.

WHY conftest.py and not per-module fixtures: the cleaning, loading and analytics
tests all need the same controlled data, and duplicating setup across three files
means a schema change requires three edits that can disagree.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.audit import AuditLog
from src.config import AS_OF_DATE, RunConfig


# ── Minimal raw DataFrames (string-typed, matching seed_data.py structure) ────

@pytest.fixture
def raw_stores_minimal() -> pd.DataFrame:
    """Three stores: one clean, one with short ZIP (ST-01), one with NULL region (ST-03)."""
    return pd.DataFrame({
        "store_id":    ["S001", "S002", "S003"],
        "store_name":  ["Alpha Store", "Beta Store", "Gamma Store"],
        "city":        ["Rochester", "Portland", "Portland"],
        "state":       ["NY", "OR", "OR"],
        "zip_code":    ["14604", "97220", "0938"],   # ST-01: S003 short ZIP
        "region":      ["Northeast", None, "West"],  # ST-03: S002 null region
        "opened_date": ["2010-01-01", "2015-06-15", "2018-03-20"],
    })


@pytest.fixture
def raw_stores_with_near_dupe() -> pd.DataFrame:
    """S007 near-duplicate: same ID, different name (ST-02)."""
    return pd.DataFrame({
        "store_id":    ["S001", "S007", "S007"],
        "store_name":  ["Alpha Store", "Downtown Rochester", "Rochester Downtown"],
        "city":        ["Rochester", "Rochester", "Rochester"],
        "state":       ["NY", "NY", "NY"],
        "zip_code":    ["14604", "14604", "14604"],
        "region":      ["Northeast", "Northeast", "Northeast"],
        "opened_date": ["2010-01-01", "2006-01-22", "2006-01-22"],
    })


@pytest.fixture
def raw_products_minimal() -> pd.DataFrame:
    """Four products: one clean, one exact dupe (PR-01), one null category (PR-03),
    one zero price (PR-04)."""
    return pd.DataFrame({
        "product_id":   ["P001", "P002", "P002", "P003"],
        "product_name": ["Widget A", "Widget B", "Widget B", "Widget C"],
        "category":     ["Electronics", "Apparel", "Apparel", None],
        "unit_price":   ["99.99", "49.50", "49.50", "0.00"],
        "supplier_id":  ["SUP001", "SUP002", "SUP002", "SUP003"],
    })


@pytest.fixture
def raw_products_with_price_conflict() -> pd.DataFrame:
    """P005 price conflict: same product_id, different unit_price (PR-02)."""
    return pd.DataFrame({
        "product_id":   ["P001", "P005", "P005"],
        "product_name": ["Widget A", "Widget E", "Widget E"],
        "category":     ["Electronics", "Apparel", "Apparel"],
        "unit_price":   ["99.99", "141.61", "150.11"],
        "supplier_id":  ["SUP001", "SUP002", "SUP002"],
    })


@pytest.fixture
def raw_transactions_minimal() -> pd.DataFrame:
    """Six transactions covering: clean sale, return (TX-10), guest (TX-06),
    future date (TX-08), zero qty (TX-07), orphan store (TX-04)."""
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002", "TXN003", "TXN004", "TXN005", "TXN006"],
        "transaction_date": [
            "2026-05-15",     # clean
            "2026-05-16",     # return (negative qty)
            "2026-05-17",     # guest checkout (null customer)
            "2026-07-01",     # future date (past AS_OF_DATE 2026-06-02)
            "2026-05-18",     # zero quantity
            "2026-05-19",     # orphan store
        ],
        "store_id":    ["S001", "S001", "S001", "S001", "S001", "S999"],
        "product_id":  ["P001", "P001", "P001", "P001", "P001", "P001"],
        "customer_id": ["CUST001", "CUST001", None, "CUST002", "CUST003", "CUST004"],
        "quantity":    ["3", "-1", "2", "1", "0", "4"],
        "unit_price":  ["99.99", "99.99", "99.99", "99.99", "99.99", "99.99"],
        "total_amount": ["299.97", "-99.99", "199.98", "99.99", "0", "399.96"],
    })


@pytest.fixture
def raw_transactions_with_discount() -> pd.DataFrame:
    """Two transactions: one clean, one with a silent discount (TX-03).
    TXN002 total_amount is 10% less than qty × price."""
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002"],
        "transaction_date": ["2026-05-15", "2026-05-16"],
        "store_id":         ["S001", "S001"],
        "product_id":       ["P001", "P001"],
        "customer_id":      ["CUST001", "CUST002"],
        "quantity":         ["2", "3"],
        "unit_price":       ["100.00", "100.00"],
        "total_amount":     ["200.00", "270.00"],  # TX-03: 300 * 0.90 = 270
    })


@pytest.fixture
def raw_transactions_mixed_dates() -> pd.DataFrame:
    """Three transactions with three date formats (TX-01)."""
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002", "TXN003"],
        "transaction_date": ["2026-05-15", "05/16/2026", "17-05-2026"],
        "store_id":         ["S001", "S001", "S001"],
        "product_id":       ["P001", "P001", "P001"],
        "customer_id":      ["CUST001", "CUST002", "CUST003"],
        "quantity":         ["1", "2", "3"],
        "unit_price":       ["50.00", "50.00", "50.00"],
        "total_amount":     ["50.00", "100.00", "150.00"],
    })


@pytest.fixture
def raw_transactions_currency_strings() -> pd.DataFrame:
    """Two transactions: one clean numeric, one with $-prefix (TX-02)."""
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002"],
        "transaction_date": ["2026-05-15", "2026-05-16"],
        "store_id":         ["S001", "S001"],
        "product_id":       ["P001", "P001"],
        "customer_id":      ["CUST001", "CUST002"],
        "quantity":         ["2", "3"],
        "unit_price":       ["100.00", "100.00"],
        "total_amount":     ["200.00", "$300.00"],
    })


# ── Audit log fixture ────────────────────────────────────────────────────────
@pytest.fixture
def audit_log() -> AuditLog:
    """A fresh AuditLog pinned to the seed's AS_OF_DATE."""
    return AuditLog(as_of_date=AS_OF_DATE)


# ── Config fixture ────────────────────────────────────────────────────────────
@pytest.fixture
def run_config(tmp_path: Path) -> RunConfig:
    """A RunConfig pointing at tmp_path for all outputs."""
    return RunConfig(
        as_of_date=AS_OF_DATE,
        raw_dir=tmp_path / "raw",
        output_dir=tmp_path / "output",
    )
