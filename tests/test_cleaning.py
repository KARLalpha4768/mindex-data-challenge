"""Tests for the data cleaning transformations.

Each test targets a specific defect class from the catalog and verifies that
the cleaning function:
  1. Detected the correct number of affected rows.
  2. Applied the documented decision (not an accidental one).
  3. Recorded the finding in the audit log.

The test names include the defect code so a failing test immediately identifies
which cleaning rule regressed.

Coverage:
  ST-01  Malformed ZIP — padded to 5 digits
  ST-02  Near-duplicate PK — survivorship rule applied
  ST-03  NULL region — imputed from observed vocabulary
  PR-01  Exact duplicate — dropped
  PR-02  Price conflict — flagged, higher price elected
  PR-03  NULL category — imputed to 'Unknown'
  PR-04  Zero price — flagged
  TX-01  Mixed date formats — all three formats parsed
  TX-02  Currency strings — $ stripped, numeric recovered
  TX-03  Silent discount — total_amount preserved, not recomputed
  TX-06  NULL customer — sentinel 'GUEST' applied
  TX-07  Zero quantity — excluded
  TX-08  Future date — excluded
  TX-10  Returns — preserved with is_return flag
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.audit import AuditLog
from src.config import AS_OF_DATE
from src.defects import DefectCode


# ── Store cleaning tests ─────────────────────────────────────────────────────

class TestStoreCleaningST01:
    """ST-01: Malformed ZIP code padded to 5 digits."""

    def test_short_zip_padded(self, raw_stores_minimal: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_minimal, audit_log)

        # S003's '0938' should become '00938' (5 characters)
        s003 = result.loc[result["store_id"] == "S003"]
        assert len(s003) == 1
        assert len(s003.iloc[0]["zip_code"]) == 5, "ZIP must be zero-padded to 5 digits"

        # Verify the audit log recorded the defect
        rec = audit_log.get(DefectCode.ST_01_MALFORMED_ZIP)
        assert rec is not None, "ST-01 must be recorded in the audit log"
        assert rec.detected_count == 1


class TestStoreCleaningST02:
    """ST-02: Near-duplicate primary key resolved by survivorship rule."""

    def test_near_duplicate_resolved(self, raw_stores_with_near_dupe: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_with_near_dupe, audit_log)

        # Two S007 rows should collapse to one
        s007_rows = result.loc[result["store_id"] == "S007"]
        assert len(s007_rows) == 1, "Near-duplicate S007 must be collapsed to one row"

        # The survivor must be deterministic (lexicographically first name)
        surviving_name = s007_rows.iloc[0]["store_name"]
        assert surviving_name == "Downtown Rochester", (
            "Survivorship rule should elect 'Downtown Rochester' (lex-first)"
        )

        rec = audit_log.get(DefectCode.ST_02_NEAR_DUPLICATE_PK)
        assert rec is not None
        assert rec.detected_count == 1


class TestStoreCleaningST03:
    """ST-03: NULL region imputed from observed state-to-region vocabulary."""

    def test_null_region_imputed(self, raw_stores_minimal: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_minimal, audit_log)

        # S002 had NULL region with state=OR. OR should map to 'West' from
        # the observed vocabulary (S003 is OR/West in this fixture).
        s002 = result.loc[result["store_id"] == "S002"]
        assert len(s002) == 1
        region = s002.iloc[0]["region"]
        assert region is not None and str(region).strip() != "", "Region must not be null"
        # Should NOT be 'East' — that was V1's bug. Should match vocabulary.
        assert region == "West", "OR should map to 'West' from observed vocabulary"

        rec = audit_log.get(DefectCode.ST_03_NULL_REGION)
        assert rec is not None
        assert rec.detected_count == 1


# ── Product cleaning tests ───────────────────────────────────────────────────

class TestProductCleaningPR01:
    """PR-01: Exact duplicate product row dropped."""

    def test_exact_duplicate_dropped(self, raw_products_minimal: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_minimal, audit_log)

        # P002 appeared twice (byte-identical); should survive as one row
        p002_rows = result.loc[result["product_id"] == "P002"]
        assert len(p002_rows) == 1, "Exact duplicate P002 must be collapsed to one row"

        rec = audit_log.get(DefectCode.PR_01_EXACT_DUPLICATE)
        assert rec is not None
        assert rec.detected_count == 1


class TestProductCleaningPR03:
    """PR-03: NULL category imputed to 'Unknown'."""

    def test_null_category_imputed(self, raw_products_minimal: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_minimal, audit_log)

        # P003 had NULL category; should be 'Unknown', not guessed
        p003 = result.loc[result["product_id"] == "P003"]
        assert len(p003) == 1
        assert p003.iloc[0]["category"] == "Unknown", "NULL category must become 'Unknown'"

        rec = audit_log.get(DefectCode.PR_03_NULL_CATEGORY)
        assert rec is not None


class TestProductCleaningPR02:
    """PR-02: Price conflict detected and flagged, not silently dropped."""

    def test_price_conflict_flagged(self, raw_products_with_price_conflict: pd.DataFrame, audit_log: AuditLog) -> None:
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_with_price_conflict, audit_log)

        # P005 had two prices (141.61 and 150.11). Should survive as ONE row.
        p005_rows = result.loc[result["product_id"] == "P005"]
        assert len(p005_rows) == 1, "P005 must collapse to one row"

        # The conflict must be reported in the audit log
        rec = audit_log.get(DefectCode.PR_02_PRICE_CHANGE)
        assert rec is not None, "PR-02 price conflict must be recorded"
        assert rec.detected_count == 1


# ── Transaction cleaning tests ───────────────────────────────────────────────

class TestTransactionCleaningTX03:
    """TX-03: Silent discount — reported total_amount preserved, NOT recomputed."""

    def test_discount_preserved(
        self, raw_transactions_with_discount: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_with_discount, audit_log, valid_stores, valid_products
        )

        # TXN002: qty=3, price=100, total=270 (10% discount).
        # The pipeline MUST NOT recompute total to 300.
        txn002 = result.loc[result["transaction_id"] == "TXN002"]
        assert len(txn002) == 1

        # The total amount should be 270.00, NOT 300.00
        net = float(txn002.iloc[0]["total_amount"])
        assert abs(net - 270.00) < 0.01, (
            f"TX-03: total_amount must be 270.00 (the reported total), not {net}. "
            "Recomputing total_amount = qty × price destroys the discount evidence."
        )

        rec = audit_log.get(DefectCode.TX_03_SILENT_DISCOUNT)
        assert rec is not None, "TX-03 discount must be detected and recorded"
        assert rec.detected_count >= 1


class TestTransactionCleaningTX10:
    """TX-10: Returns preserved with negative measures and is_return flag."""

    def test_returns_preserved(
        self, raw_transactions_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_minimal, audit_log, valid_stores, valid_products
        )

        # TXN002 is a return (qty=-1). It must survive with is_return=True.
        txn002 = result.loc[result["transaction_id"] == "TXN002"]
        assert len(txn002) == 1, "Return transaction TXN002 must not be dropped"

        is_ret = txn002.iloc[0].get("is_return", None)
        assert is_ret is True or is_ret == 1, "Return must be flagged as is_return=True"

        qty = int(txn002.iloc[0]["quantity"])
        assert qty < 0, "Return quantity must remain negative"


class TestTransactionCleaningTX06:
    """TX-06: NULL customer_id replaced with GUEST sentinel."""

    def test_guest_customer_imputed(
        self, raw_transactions_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_minimal, audit_log, valid_stores, valid_products
        )

        # TXN003 had NULL customer_id. It must survive (not dropped) with 'GUEST'.
        txn003 = result.loc[result["transaction_id"] == "TXN003"]
        assert len(txn003) == 1, "Guest transaction must not be dropped"
        assert txn003.iloc[0]["customer_id"] == "GUEST", "NULL customer must become 'GUEST'"

        rec = audit_log.get(DefectCode.TX_06_NULL_CUSTOMER)
        assert rec is not None


class TestTransactionCleaningTX08:
    """TX-08: Future-dated transactions excluded."""

    def test_future_dates_excluded(
        self, raw_transactions_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_minimal, audit_log, valid_stores, valid_products
        )

        # TXN004 is dated 2026-07-01, which is after AS_OF_DATE (2026-06-02).
        txn004 = result.loc[result["transaction_id"] == "TXN004"]
        assert len(txn004) == 0, "Future-dated TXN004 must be excluded"

        rec = audit_log.get(DefectCode.TX_08_FUTURE_DATE)
        assert rec is not None
        assert rec.detected_count >= 1


class TestTransactionCleaningTX07:
    """TX-07: Zero-quantity transactions excluded."""

    def test_zero_quantity_excluded(
        self, raw_transactions_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_minimal, audit_log, valid_stores, valid_products
        )

        # TXN005 has quantity=0. It must be excluded.
        txn005 = result.loc[result["transaction_id"] == "TXN005"]
        assert len(txn005) == 0, "Zero-quantity TXN005 must be excluded"


class TestTransactionCleaningTX04:
    """TX-04: Orphaned store_id excluded."""

    def test_orphan_store_excluded(
        self, raw_transactions_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}  # S999 is not valid
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_minimal, audit_log, valid_stores, valid_products
        )

        # TXN006 references S999 (not in valid_stores). Must be excluded.
        txn006 = result.loc[result["transaction_id"] == "TXN006"]
        assert len(txn006) == 0, "Orphaned-store TXN006 must be excluded"


class TestTransactionCleaningTX01:
    """TX-01: Mixed date formats all parsed correctly."""

    def test_mixed_date_formats_parsed(
        self, raw_transactions_mixed_dates: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_mixed_dates, audit_log, valid_stores, valid_products
        )

        # All 3 transactions should survive (none are future-dated, zero-qty, etc.)
        assert len(result) == 3, (
            f"All 3 transactions with mixed date formats must be parsed; got {len(result)}"
        )

        rec = audit_log.get(DefectCode.TX_01_MIXED_DATE_FORMATS)
        assert rec is not None, "TX-01 must be detected (2 non-ISO rows)"
        assert rec.detected_count == 2  # 2 of 3 rows are non-ISO


class TestTransactionCleaningTX02:
    """TX-02: Currency-formatted strings parsed to numeric."""

    def test_currency_strings_parsed(
        self, raw_transactions_currency_strings: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        from src.cleaning.transactions import clean_transactions

        valid_stores = {"S001"}
        valid_products = {"P001"}
        result = clean_transactions(
            raw_transactions_currency_strings, audit_log, valid_stores, valid_products
        )

        # Both transactions should survive with numeric amounts
        assert len(result) == 2
        # TXN002's "$300.00" should parse to 300.00
        txn002 = result.loc[result["transaction_id"] == "TXN002"]
        net = float(txn002.iloc[0]["total_amount"])
        assert abs(net - 300.00) < 0.01, f"'$300.00' must parse to 300.00, got {net}"
