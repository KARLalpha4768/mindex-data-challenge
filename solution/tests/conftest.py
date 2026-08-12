"""Shared pytest fixtures for the Mindex pipeline test suite.

Every fixture that a test needs — controlled DataFrames, temporary databases,
a pre-loaded warehouse, a populated audit log — lives here so individual test
modules stay focused on assertions rather than setup.

Fixture naming convention:
  raw_*         — raw (string-typed) DataFrames matching seed_data.py's output.
  clean_*       — post-cleaning DataFrames, suitable for loading.
  db_*          — paths to SQLite databases.
  warehouse_*   — connections or paths to fully loaded warehouses.
  golden_*      — one real, end-to-end pipeline run over ``data/raw/`` (F12).

WHERE TESTS ARE ALLOWED TO WRITE (F3, verification report finding)
------------------------------------------------------------------
Nowhere except ``tmp_path`` / ``tmp_path_factory``. The suite used to call
``clean_transactions`` without ``lineage_dir``, which defaulted to the
import-time project path, so every ``pytest`` run overwrote the deliverable's
505-row ``output/quarantine/transactions__lineage.csv`` with 2 rows of fixture
data. ``lineage_dir`` is now a required keyword argument; pass ``tmp_path``
(or an explicit ``None``) at every call site and never a real project path.

WHY conftest.py and not per-module fixtures: the cleaning, loading and analytics
tests all need the same controlled data, and duplicating setup across three files
means a schema change requires three edits that can disagree.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.analytics.queries import METRIC_REGISTRY
from src.audit import AuditLog
from src.config import AS_OF_DATE, RAW_DIR, RunConfig
from src.pipeline import PipelineResult, run_pipeline


# ── Marker registration ──────────────────────────────────────────────────────
# WHY here and not pyproject.toml: pyproject is owned by another agent, and
# ``addopts = "--strict-markers"`` makes an unregistered marker a hard error.
# Registering from conftest keeps the marker and the tests that use it in the
# same ownership boundary.
def pytest_configure(config: pytest.Config) -> None:
    """Register suite-local markers.

    ``golden`` selects the end-to-end tests that run the real pipeline over
    ``data/raw/`` (F12). It exists for *selection* (``pytest -m golden``), NOT
    for exclusion — the golden tests run by default, because a golden test
    behind an opt-in flag is not a golden test.
    """
    config.addinivalue_line(
        "markers",
        "golden: end-to-end assertions against the real data/raw/ CSVs (finding F12).",
    )


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


# ══════════════════════════════════════════════════════════════════════════════
# F12 · Golden end-to-end run against the REAL data/raw/ CSVs
# ══════════════════════════════════════════════════════════════════════════════
# WHY this exists: before it, the suite was 100% synthetic fixtures — the
# verification report's F12 — so nothing would have noticed if kept rows moved
# from 474 to 400 or net revenue moved by $10k. Three of the five surviving
# mutations (M2, M5, M18) are killed by pinning real end-to-end numbers.
#
# WHY session-scoped: the run takes a couple of seconds (read → profile → clean
# → SQLite load → six SQL metrics → four JSON artifacts). Repeating it per test
# would make the golden module the slowest thing in the suite for no extra
# assurance — the run is deterministic, so one run serves every assertion.
#
# WHY tmp_path_factory and never OUTPUT_DIR: see the module header. A golden
# test that wrote into the deliverable would be a worse bug than the one it
# defends against. ``tmp_path_factory`` is also on local disk, which matters:
# the warehouse loader unlinks a temp file and the project tree is on a mount
# that denies unlink.

@pytest.fixture(scope="session")
def golden_raw_dir() -> Path:
    """The real, immutable ``data/raw/`` directory shipped with the challenge.

    Skips the whole golden module rather than erroring if the CSVs are absent,
    so a partial checkout still runs the synthetic suite.
    """
    if not (RAW_DIR / "transactions.csv").exists():
        pytest.skip(f"real raw CSVs not found under {RAW_DIR}")
    return RAW_DIR


@pytest.fixture(scope="session")
def golden_run(golden_raw_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> PipelineResult:
    """Execute the full pipeline once over the real CSVs into a temp directory.

    Returns:
        The :class:`~src.pipeline.PipelineResult`, which carries the audit
        ledger, the analytics payload, the row counts and the coverage
        mismatches — everything the golden assertions need, in process, with no
        JSON round-trip.
    """
    output_dir = tmp_path_factory.mktemp("golden_run")
    cfg = RunConfig(
        as_of_date=AS_OF_DATE,
        raw_dir=golden_raw_dir,
        output_dir=output_dir,
    )
    return run_pipeline(cfg)


@pytest.fixture(scope="session")
def golden_conn(golden_run: PipelineResult) -> Iterator[sqlite3.Connection]:
    """A read-only-in-spirit connection to the golden run's warehouse.

    ``row_factory`` is set so tests can index columns by name, which keeps the
    assertions readable and makes a column rename fail loudly instead of
    silently shifting a positional index.
    """
    conn = sqlite3.connect(str(golden_run.config.db_path))
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def golden_lineage(golden_run: PipelineResult) -> pd.DataFrame:
    """The 505-row row-level disposition file produced by the golden run."""
    path = golden_run.config.quarantine_dir / "transactions__lineage.csv"
    assert path.exists(), (
        f"transactions__lineage.csv is missing from {golden_run.config.quarantine_dir}. "
        "F3: the lineage artifact must follow --output-dir, not an import-time constant."
    )
    return pd.read_csv(path)


# ── Metric lookup helper ─────────────────────────────────────────────────────
def metric_sql(metric_id: str) -> str:
    """Return the SQL text registered under ``metric_id``.

    WHY tests resolve SQL through the registry rather than importing the module
    constant by name: the metric id is the binding interface (contract §6 and
    verification finding F11), the Python constant name is not. Looking up by id
    means a rename of the *id* — which changes analytics.json, the dashboard and
    the README — cannot pass unnoticed, while a purely internal constant rename
    does not churn the suite.

    Args:
        metric_id: A contract §6 metric id, e.g. ``"aov_by_region"``.

    Returns:
        The query string.

    Raises:
        KeyError: With the full list of registered ids, so a failure names the
            fix instead of just the symptom.
    """
    try:
        return METRIC_REGISTRY[metric_id]["sql"]
    except KeyError:
        raise KeyError(
            f"METRIC_REGISTRY has no metric id {metric_id!r}. "
            f"Registered: {sorted(METRIC_REGISTRY)}. "
            "Contract §6 fixes these ids as a binding interface (F11)."
        ) from None


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures for the three defect codes that had NO test at all (F8)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def raw_transactions_orphan_product() -> pd.DataFrame:
    """Four transactions, two of which reference products not in the dimension.

    TX-05 had **zero** mentions anywhere in ``tests/`` before this fixture,
    while ``README.md`` claimed the suite covered "orphan exclusions
    (TX-04/05)". P031 and P032 mirror the real seeded orphan ids.
    """
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002", "TXN003", "TXN004"],
        "transaction_date": ["2026-05-15", "2026-05-16", "2026-05-17", "2026-05-18"],
        "store_id":         ["S001", "S001", "S001", "S001"],
        "product_id":       ["P001", "P031", "P032", "P001"],   # TX-05: P031, P032
        "customer_id":      ["CUST001", "CUST002", "CUST003", "CUST004"],
        "quantity":         ["2", "3", "1", "4"],
        "unit_price":       ["50.00", "50.00", "50.00", "50.00"],
        "total_amount":     ["100.00", "150.00", "50.00", "200.00"],
    })


@pytest.fixture
def raw_transactions_exact_duplicates() -> pd.DataFrame:
    """Five source rows, of which two are byte-identical copies of earlier rows.

    TX-09 also had zero mentions in ``tests/``. It is caught in production only
    by the warehouse's UNIQUE(transaction_id) constraint (mutation M9), which is
    a fine backstop but is not a test of the cleaner's own dedup rule.
    """
    return pd.DataFrame({
        "transaction_id":   ["TXN001", "TXN002", "TXN001", "TXN003", "TXN002"],
        "transaction_date": ["2026-05-15", "2026-05-16", "2026-05-15",
                             "2026-05-17", "2026-05-16"],
        "store_id":         ["S001", "S001", "S001", "S001", "S001"],
        "product_id":       ["P001", "P001", "P001", "P001", "P001"],
        "customer_id":      ["CUST001", "CUST002", "CUST001", "CUST003", "CUST002"],
        "quantity":         ["2", "3", "2", "1", "3"],
        "unit_price":       ["50.00", "50.00", "50.00", "50.00", "50.00"],
        "total_amount":     ["100.00", "150.00", "100.00", "50.00", "150.00"],
    })


@pytest.fixture
def raw_products_zero_price() -> pd.DataFrame:
    """Four Apparel products, one of which lists at 0.00 (PR-04).

    Prices 100 / 120 / 140 give a category median of **120.00**, which is the
    value P027 must receive — a specific, hand-checkable number rather than
    "something non-zero". PR-04 previously appeared in ``tests/`` only inside a
    docstring that claimed it was covered.
    """
    return pd.DataFrame({
        "product_id":   ["P024", "P025", "P026", "P027"],
        "product_name": ["Widget X", "Widget Y", "Widget Z", "Widget Q"],
        "category":     ["Apparel", "Apparel", "Apparel", "Apparel"],
        "unit_price":   ["100.00", "120.00", "140.00", "0.00"],   # PR-04: P027
        "supplier_id":  ["SUP001", "SUP001", "SUP002", "SUP002"],
    })


@pytest.fixture
def raw_stores_unpaddable_zip() -> pd.DataFrame:
    """Four stores whose ZIPs exercise every branch of the ST-01 digit guard.

    - S001 ``"14604"``  — well formed, must be untouched and not flagged.
    - S003 ``"0938"``   — short but all digits, must pad to ``"00938"``.
    - S020 ``"N/A"``    — not paddable; zero-filling it yields ``"00N/A"``,
      a ZIP that never existed in any encoding of the source.
    - S021 ``"14604-1234"`` — ZIP+4, longer than 5; ``zfill`` is a no-op here
      but a truncating "fix" would be just as wrong, so it is pinned too.
    """
    return pd.DataFrame({
        "store_id":    ["S001", "S003", "S020", "S021"],
        "store_name":  ["Alpha Store", "Gamma Store", "Delta Store", "Epsilon Store"],
        "city":        ["Rochester", "Greece", "Buffalo", "Albany"],
        "state":       ["NY", "NY", "NY", "NY"],
        "zip_code":    ["14604", "0938", "N/A", "14604-1234"],
        "region":      ["Northeast", "Northeast", "Northeast", "Northeast"],
        "opened_date": ["2010-01-01", "2012-06-20", "2014-02-02", "2016-03-03"],
    })
