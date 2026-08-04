"""Tests for the data cleaning transformations.

Each test targets a specific defect class from the catalog and verifies that
the cleaning function:
  1. Detected the correct number of affected rows.
  2. Applied the documented decision (not an accidental one).
  3. Recorded the finding in the audit log.

The test names include the defect code so a failing test immediately identifies
which cleaning rule regressed.

Coverage in THIS module:
  ST-01  Malformed ZIP — '0938' padded to '00938', flagged unverifiable
  ST-02  Near-duplicate PK — three-stage survivorship rule, order-independent
  ST-03  NULL region — imputed from the column's own observed vocabulary
  PR-01  Exact duplicate — dropped
  PR-02  Price conflict — MAX elected, flagged, not misfiled as a duplicate
  PR-03  NULL category — imputed to the literal 'Unknown'
  TX-01  Mixed date formats — all three formats parsed to the CORRECT calendar date
  TX-02  Currency strings — $ stripped, numeric recovered
  TX-03  Silent discount — total_amount preserved, discount exposed
  TX-04  Orphan store — quarantined
  TX-06  NULL customer — sentinel 'GUEST' applied, row kept
  TX-07  Zero quantity — quarantined
  TX-08  Future date — quarantined
  TX-10  Returns — preserved with is_return flag and negative money

Covered elsewhere, deliberately:
  PR-04, TX-05, TX-09        -> ``tests/test_defect_gaps.py``
  ST-01's non-paddable guard -> ``tests/test_defect_gaps.py`` (F14 / mutation M12)
  End-to-end real-data pins  -> ``tests/test_golden_end_to_end.py`` (F12)

WHY THIS LIST IS NOW ACCURATE (finding F8)
------------------------------------------
The previous version of this header claimed "PR-04 Zero price — flagged" while
no PR-04 assertion existed anywhere in the suite, and README.md repeated the
claim. A docstring that names a defect code is a claim a reviewer will act on;
if the assertion is not there, the docstring is not documentation, it is a
false statement. Every code listed above now has at least one assertion in this
file, and the three that do not are pointed at the file that does test them.

WHERE THESE TESTS WRITE (finding F3)
------------------------------------
``clean_transactions`` requires an explicit ``lineage_dir``. Every call site
below passes ``tmp_path``. Before this was required the parameter defaulted to
the import-time project path, so running the suite overwrote the deliverable's
own 505-row ``output/quarantine/transactions__lineage.csv`` with 2 rows of
fixture data.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from src.audit import AuditLog
from src.config import AS_OF_DATE
from src.defects import DefectCode


# ── Store cleaning tests ─────────────────────────────────────────────────────

class TestStoreCleaningST01:
    """ST-01: Malformed ZIP code padded to 5 digits."""

    def test_short_zip_padded(self, raw_stores_minimal: pd.DataFrame, audit_log: AuditLog) -> None:
        """S003's '0938' becomes exactly '00938' and is flagged as unverifiable.

        STRENGTHENED (F14): this test previously asserted only
        ``len(zip_code) == 5``, which any unconditional ``zfill(5)`` satisfies —
        including mutation M12. It now pins the exact value, pins that the
        already-valid ZIPs are untouched, and pins the ``zip_is_suspect`` flag,
        because '00938' is a Puerto Rico range value on a store in Greece, NY:
        padding restores a well-formed field, not a true one.
        """
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_minimal, audit_log)
        by_id = result.set_index("store_id")

        assert by_id.loc["S003", "zip_code"] == "00938"
        assert bool(by_id.loc["S003", "zip_is_suspect"]) is True

        # The two already-valid ZIPs must be passed through byte for byte, and
        # must NOT inherit the suspicion flag.
        assert by_id.loc["S001", "zip_code"] == "14604"
        assert by_id.loc["S002", "zip_code"] == "97220"
        assert not by_id.loc[["S001", "S002"], "zip_is_suspect"].astype(bool).any()

        rec = audit_log.get(DefectCode.ST_01_MALFORMED_ZIP)
        assert rec is not None, "ST-01 must be recorded in the audit log"
        assert rec.detected_count == 1
        assert rec.affected_keys == ["S003"]
        assert rec.action == "flagged"

    def test_no_store_is_lost_to_a_formatting_defect(
        self, raw_stores_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """All three stores survive ST-01.

        Dropping S003 would trade a cosmetic problem for a revenue problem —
        the store has real transactions. Nothing else pins the row count of the
        store dimension after ZIP handling.
        """
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_minimal, audit_log)
        assert result["store_id"].tolist() == ["S001", "S002", "S003"]


class TestStoreCleaningST02:
    """ST-02: Near-duplicate primary key resolved by survivorship rule."""

    def test_near_duplicate_resolved(
        self, raw_stores_with_near_dupe: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """S007 collapses to one row and 'Downtown Rochester' is elected.

        CORRECTED (F17): the previous version of this test described the rule as
        a "lexicographical tie-breaker". The implemented — and contracted — rule
        is three stages: fewest nulls, then earliest ``opened_date``, then
        lexicographic ``store_name``. Lexicographic order only decides this
        fixture because the first two stages tie.
        """
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_with_near_dupe, audit_log)

        s007_rows = result.loc[result["store_id"] == "S007"]
        assert len(s007_rows) == 1, "Near-duplicate S007 must be collapsed to one row"
        assert s007_rows.iloc[0]["store_name"] == "Downtown Rochester"
        assert len(result) == 2, "S001 must survive alongside the elected S007"

        rec = audit_log.get(DefectCode.ST_02_NEAR_DUPLICATE_PK)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "dropped"

    def test_survivor_does_not_depend_on_source_row_order(
        self, raw_stores_with_near_dupe: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """Reversing the two S007 rows elects the same survivor.

        STRENGTHENED: the fixture happens to list 'Downtown Rochester' first, so
        a blind ``drop_duplicates(keep="first")`` — the exact anti-pattern the
        contract forbids — passes the test above. Feeding the rows in the
        opposite order is what distinguishes an explicit ranked rule from file
        order. Without this test, "documented survivorship rule" is unverified.
        """
        from src.cleaning.stores import clean_stores

        reordered = raw_stores_with_near_dupe.iloc[[0, 2, 1]].reset_index(drop=True)
        assert reordered.loc[1, "store_name"] == "Rochester Downtown", "fixture guard"

        result = clean_stores(reordered, audit_log)
        s007_rows = result.loc[result["store_id"] == "S007"]

        assert len(s007_rows) == 1
        assert s007_rows.iloc[0]["store_name"] == "Downtown Rochester", (
            "the elected golden record must be decided by the ranked rule, not by "
            "which row the source file happened to list first"
        )


class TestStoreCleaningST03:
    """ST-03: NULL region imputed from observed state-to-region vocabulary."""

    def test_null_region_imputed(
        self, raw_stores_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """S002 (OR) becomes 'West', flagged as imputed, and no new region is invented.

        STRENGTHENED: now asserts ``region_is_imputed`` per row and asserts the
        whole region vocabulary, not just the one value. The previous
        solution's bug #3 mapped NY to an invented "East" that split Northeast
        in two and corrupted AOV-by-region; a value-only assertion on S002 would
        not have noticed a different row acquiring a fabricated region.
        """
        from src.cleaning.stores import clean_stores

        result = clean_stores(raw_stores_minimal, audit_log)
        by_id = result.set_index("store_id")

        assert by_id.loc["S002", "region"] == "West"
        assert bool(by_id.loc["S002", "region_is_imputed"]) is True

        # Imputation may only use vocabulary the column already contained.
        assert set(result["region"]) <= {"Northeast", "West"}
        assert "East" not in set(result["region"]), (
            "inventing 'East' is the previous solution's bug #3 — it splits Northeast "
            "in two and silently corrupts every region-level metric"
        )
        assert not by_id.loc[["S001", "S003"], "region_is_imputed"].astype(bool).any()

        rec = audit_log.get(DefectCode.ST_03_NULL_REGION)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "imputed"


# ── Product cleaning tests ───────────────────────────────────────────────────

class TestProductCleaningPR01:
    """PR-01: Exact duplicate product row dropped."""

    def test_exact_duplicate_dropped(
        self, raw_products_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """The byte-identical P002 copy is dropped; the other three products survive.

        STRENGTHENED: also pins the total row count and the surviving payload.
        "P002 appears once" is true both when the duplicate is dropped and when
        an over-eager key-based dedup swallows a genuine conflict — which is
        precisely how PR-02 was lost in the previous solution.
        """
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_minimal, audit_log)

        assert result["product_id"].tolist() == ["P001", "P002", "P003"]
        assert result["product_id"].is_unique
        assert float(result.set_index("product_id").loc["P002", "list_unit_price"]) == pytest.approx(
            49.50, abs=0.005
        )

        rec = audit_log.get(DefectCode.PR_01_EXACT_DUPLICATE)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "dropped"
        assert rec.affected_keys == ["P002"]


class TestProductCleaningPR03:
    """PR-03: NULL category imputed to 'Unknown'."""

    def test_null_category_imputed(
        self, raw_products_minimal: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """P003's missing category becomes the literal 'Unknown', flagged as imputed.

        STRENGTHENED: the previous version asserted only that a record existed
        (``rec is not None``) with no count and no flag. It now pins the count,
        the action, and that no *other* product's category was rewritten — the
        contract forbids guessing a category, and a rule that guessed would
        still leave P003 non-null.
        """
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_minimal, audit_log)
        by_id = result.set_index("product_id")

        assert by_id.loc["P003", "category"] == "Unknown"
        assert bool(by_id.loc["P003", "category_is_imputed"]) is True

        assert by_id.loc["P001", "category"] == "Electronics"
        assert by_id.loc["P002", "category"] == "Apparel"
        assert not by_id.loc[["P001", "P002"], "category_is_imputed"].astype(bool).any()

        rec = audit_log.get(DefectCode.PR_03_NULL_CATEGORY)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "imputed"


class TestProductCleaningPR02:
    """PR-02: Price conflict detected, flagged, and resolved by an explicit MAX policy."""

    def test_price_conflict_flagged(
        self, raw_products_with_price_conflict: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """P005 collapses to one row carrying the ELECTED price 150.11.

        STRENGTHENED (mutation M2): this test previously asserted only that one
        row survived and that a record existed. Inverting the resolution policy
        from MAX to MIN therefore passed it, passed the whole suite, and still
        printed "PR-02 · Expected 1 · Detected 1 · OK" in the coverage table —
        the most dangerous of the five surviving mutations, because the
        completeness proof gave false assurance. See
        ``tests/test_defect_gaps.py::TestProductCleaningPR02ElectsMax`` for the
        mis-filing check that goes with it.
        """
        from src.cleaning.products import clean_products

        result = clean_products(raw_products_with_price_conflict, audit_log)

        p005_rows = result.loc[result["product_id"] == "P005"]
        assert len(p005_rows) == 1, "P005 must collapse to one row"
        assert float(p005_rows.iloc[0]["list_unit_price"]) == pytest.approx(150.11, abs=0.005), (
            "PR-02 elects the higher of the two conflicting list prices (141.61 vs 150.11) "
            "by an explicit MAX policy, never by file order."
        )
        assert bool(p005_rows.iloc[0]["price_conflict"]) is True, (
            "the ambiguity must travel with the row into the warehouse"
        )

        rec = audit_log.get(DefectCode.PR_02_PRICE_CHANGE)
        assert rec is not None, "PR-02 price conflict must be recorded"
        assert rec.detected_count == 1
        assert rec.affected_keys == ["P005"]


# ── Transaction cleaning tests ───────────────────────────────────────────────

class TestTransactionCleaningTX03:
    """TX-03: Silent discount — reported total_amount preserved, NOT recomputed."""

    def test_discount_preserved(
        self,
        raw_transactions_with_discount: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN002 keeps its reported $270.00 and exposes a $30.00 discount.

        STRENGTHENED: also pins ``extended_amount``, ``discount_amount`` and
        ``has_discount``. Preserving the total is only half the decision — the
        other half is making the gap visible. A pipeline that kept 270.00 but
        reported no discount would pass the old assertion while erasing the
        finding from every downstream report.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_with_discount,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        txn002 = result.loc[result["transaction_id"] == "TXN002"]
        assert len(txn002) == 1
        row = txn002.iloc[0]

        assert float(row["total_amount"]) == pytest.approx(270.00, abs=0.005), (
            "TX-03: total_amount must be the reported total. Recomputing it as "
            "qty × price would read 300.00 and overstate revenue."
        )
        assert float(row["extended_amount"]) == pytest.approx(300.00, abs=0.005)
        assert float(row["discount_amount"]) == pytest.approx(30.00, abs=0.005)
        assert bool(row["has_discount"]) is True

        # The clean row must NOT be flagged, or the flag means nothing.
        txn001 = result.loc[result["transaction_id"] == "TXN001"].iloc[0]
        assert float(txn001["discount_amount"]) == pytest.approx(0.0, abs=0.005)
        assert bool(txn001["has_discount"]) is False

        rec = audit_log.get(DefectCode.TX_03_SILENT_DISCOUNT)
        assert rec is not None, "TX-03 discount must be detected and recorded"
        assert rec.detected_count == 1
        assert rec.action == "preserved"


class TestTransactionCleaningTX10:
    """TX-10: Returns preserved with negative measures and is_return flag."""

    def test_returns_preserved(
        self,
        raw_transactions_minimal: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN002 survives as a return with quantity −1 and total −$99.99.

        STRENGTHENED: the previous assertion was ``is_ret is True or is_ret == 1``
        plus ``qty < 0``, which a row of −1 units at $0.00 would satisfy. The
        signed money is the point: returns must *reduce* revenue, and a return
        whose amount was coerced positive would inflate it.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_minimal,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        txn002 = result.loc[result["transaction_id"] == "TXN002"]
        assert len(txn002) == 1, "Return transaction TXN002 must not be dropped"
        row = txn002.iloc[0]

        assert bool(row["is_return"]) is True
        assert int(row["quantity"]) == -1
        assert float(row["total_amount"]) == pytest.approx(-99.99, abs=0.005)

        # A sale must not be mistaken for a return.
        assert bool(result.loc[result["transaction_id"] == "TXN001"].iloc[0]["is_return"]) is False

        rec = audit_log.get(DefectCode.TX_10_RETURNS)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "preserved"


class TestTransactionCleaningTX06:
    """TX-06: NULL customer_id replaced with GUEST sentinel."""

    def test_guest_customer_imputed(
        self,
        raw_transactions_minimal: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN003 is kept, its customer becomes 'GUEST', and ``is_guest`` is set.

        STRENGTHENED: now pins the count and asserts that the paired ``is_guest``
        flag is True for the guest row and False for the others. Without the
        flag the analytics layer cannot exclude guests from the customer
        leaderboard while still counting their money everywhere else, and
        mutation M15 (renaming the sentinel) is only half-caught.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_minimal,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        txn003 = result.loc[result["transaction_id"] == "TXN003"]
        assert len(txn003) == 1, "Guest transaction must not be dropped"
        assert txn003.iloc[0]["customer_id"] == "GUEST"
        assert bool(txn003.iloc[0]["is_guest"]) is True

        others = result.loc[result["transaction_id"] != "TXN003"]
        assert not others["is_guest"].astype(bool).any()
        assert "GUEST" not in set(others["customer_id"])

        rec = audit_log.get(DefectCode.TX_06_NULL_CUSTOMER)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "imputed"


class TestTransactionCleaningTX08:
    """TX-08: Future-dated transactions excluded."""

    def test_future_dates_excluded(
        self,
        raw_transactions_minimal: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN004 (2026-07-01, past AS_OF_DATE) is quarantined under TX-08.

        STRENGTHENED: the count was ``>= 1``, which cannot distinguish "found
        the one future row" from "quarantined half the file". It is now exactly
        1, the reason code is pinned, and no surviving row is dated after
        AS_OF_DATE.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_minimal,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert "TXN004" not in set(result["transaction_id"])
        assert result["transaction_date"].max() <= pd.Timestamp(AS_OF_DATE)

        rec = audit_log.get(DefectCode.TX_08_FUTURE_DATE)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "quarantined"
        assert rec.affected_keys == ["TXN004"]

        lineage = result.attrs["_cleaning_lineage"]
        filed = dict(zip(lineage["transaction_id"], lineage["reason_code"]))
        assert filed["TXN004"] == "TX-08"


class TestTransactionCleaningTX07:
    """TX-07: Zero-quantity transactions excluded."""

    def test_zero_quantity_excluded(
        self,
        raw_transactions_minimal: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN005 (quantity 0) is quarantined and recorded under TX-07.

        STRENGTHENED: the previous test made no audit assertion at all, so a
        pipeline that dropped the row silently — without logging or quarantining
        it — passed. Silent drops are the failure mode this whole submission
        argues against.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_minimal,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert "TXN005" not in set(result["transaction_id"])
        assert (result["quantity"].astype(int) != 0).all()

        rec = audit_log.get(DefectCode.TX_07_ZERO_QUANTITY)
        assert rec is not None, "a zero-quantity row must never be dropped silently"
        assert rec.detected_count == 1
        assert rec.action == "quarantined"
        assert rec.affected_keys == ["TXN005"]


class TestTransactionCleaningTX04:
    """TX-04: Orphaned store_id excluded."""

    def test_orphan_store_excluded(
        self,
        raw_transactions_minimal: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TXN006 (store S999) is quarantined under TX-04, not loaded against 'Unknown'.

        STRENGTHENED: previously asserted only that the row was gone, with no
        audit check and no reason code. Referential integrity failures must be
        attributable — "which rows, and why" is the whole point of quarantine.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_minimal,
            audit_log,
            {"S001"},   # S999 is not valid
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert "TXN006" not in set(result["transaction_id"])
        assert set(result["store_id"]) == {"S001"}

        rec = audit_log.get(DefectCode.TX_04_ORPHAN_STORE)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.action == "quarantined"
        assert rec.affected_keys == ["TXN006"]

        lineage = result.attrs["_cleaning_lineage"]
        filed = dict(zip(lineage["transaction_id"], lineage["reason_code"]))
        assert filed["TXN006"] == "TX-04"


class TestTransactionCleaningTX01:
    """TX-01: Mixed date formats all parsed to the correct calendar date."""

    def test_mixed_date_formats_parsed(
        self,
        raw_transactions_mixed_dates: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """All three rows survive AND land on the right day.

        STRENGTHENED — this was the weakest test in the suite. It asserted
        ``len(result) == 3`` and a detection count, and nothing about the parsed
        values. TX-01 is not a "rows go missing" defect, it is a *misparse*
        defect: '05/16/2026' read as day-first becomes 2026-04-05 — a different
        month, a different quarter — and the row count is completely unchanged.
        Survival is not correctness.

        The two injected formats are separator-disjoint — '/' means US
        month-first, '-' with a two-digit head means EU day-first — which is why
        ``config.DATE_FORMATS`` is an ordered tuple and why the disambiguation
        is deterministic rather than lucky.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_mixed_dates,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert len(result) == 3, (
            f"All 3 transactions with mixed date formats must be parsed; got {len(result)}"
        )
        parsed = dict(zip(result["transaction_id"], result["transaction_date"]))
        assert parsed["TXN001"] == pd.Timestamp("2026-05-15"), "ISO '2026-05-15'"
        assert parsed["TXN002"] == pd.Timestamp("2026-05-16"), "US '05/16/2026' is 16 May"
        assert parsed["TXN003"] == pd.Timestamp("2026-05-17"), "EU '17-05-2026' is 17 May"

        assert result["transaction_date"].notna().all(), (
            "a single pd.to_datetime(errors='coerce') call NaTs these rows silently — "
            "that is the previous solution's bug #2, which then mis-attributed the 20 "
            "lost rows to 'future dates'"
        )

        rec = audit_log.get(DefectCode.TX_01_MIXED_DATE_FORMATS)
        assert rec is not None, "TX-01 must be detected (2 non-ISO rows)"
        assert rec.detected_count == 2  # 2 of 3 rows are non-ISO
        assert sorted(rec.affected_keys) == ["TXN002", "TXN003"]

    def test_ambiguous_dates_resolve_by_separator_not_by_luck(
        self, audit_log: AuditLog, tmp_path: Path
    ) -> None:
        """'01-04-2026' and '04/01/2026' both mean 2026-04-01, by different rules.

        These are the two genuinely ambiguous strings in the real file: each is
        a valid date under both US and EU readings. The separator is what
        disambiguates them, and getting one wrong shifts a transaction by three
        months without changing any row count. Nothing in the suite tested the
        ambiguous case before.
        """
        from src.cleaning.transactions import clean_transactions

        df = pd.DataFrame({
            "transaction_id":   ["TXN_EU", "TXN_US"],
            "transaction_date": ["01-04-2026", "04/01/2026"],
            "store_id":         ["S001", "S001"],
            "product_id":       ["P001", "P001"],
            "customer_id":      ["CUST001", "CUST002"],
            "quantity":         ["1", "1"],
            "unit_price":       ["50.00", "50.00"],
            "total_amount":     ["50.00", "50.00"],
        })
        result = clean_transactions(df, audit_log, {"S001"}, {"P001"}, lineage_dir=tmp_path)

        parsed = dict(zip(result["transaction_id"], result["transaction_date"]))
        assert parsed["TXN_EU"] == pd.Timestamp(dt.date(2026, 4, 1)), (
            "'01-04-2026' uses '-', so it is DD-MM-YYYY: 1 April 2026"
        )
        assert parsed["TXN_US"] == pd.Timestamp(dt.date(2026, 4, 1)), (
            "'04/01/2026' uses '/', so it is MM/DD/YYYY: 1 April 2026"
        )


class TestTransactionCleaningTX02:
    """TX-02: Currency-formatted strings parsed to numeric."""

    def test_currency_strings_parsed(
        self,
        raw_transactions_currency_strings: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """'$300.00' becomes the float 300.00, and the column is genuinely numeric.

        STRENGTHENED: also asserts the dtype and the untouched row. A parser
        that left the value as the string '$300.00' would still satisfy a
        ``float(...)`` cast inside the test while poisoning every SUM downstream;
        pinning the dtype is what makes the conversion real rather than
        test-local.
        """
        from src.cleaning.transactions import clean_transactions

        result = clean_transactions(
            raw_transactions_currency_strings,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert len(result) == 2
        assert pd.api.types.is_float_dtype(result["total_amount"])

        by_id = result.set_index("transaction_id")["total_amount"].astype(float)
        assert by_id["TXN002"] == pytest.approx(300.00, abs=0.005)
        assert by_id["TXN001"] == pytest.approx(200.00, abs=0.005)
        assert float(result["total_amount"].sum()) == pytest.approx(500.00, abs=0.005)

        rec = audit_log.get(DefectCode.TX_02_STRING_CURRENCY)
        assert rec is not None
        assert rec.detected_count == 1
        assert rec.affected_keys == ["TXN002"]
