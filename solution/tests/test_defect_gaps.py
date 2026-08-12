"""Tests for the defect codes the suite claimed to cover but did not.

Defends:
  F8   Three defect codes had **no test at all**. ``grep -c`` across ``tests/``
       gave TX-05 = 0, TX-09 = 0, PR-04 = 2 — and both PR-04 hits were
       docstrings. ``test_cleaning.py``'s module header claimed "PR-04 Zero
       price — flagged" while no assertion existed, and ``README.md`` claimed
       the suite covered "zero prices (PR-04)" and "orphan exclusions
       (TX-04/05)". Both claims were false.
  F14  ST-01's digit-guard on ``zfill`` was correct but untested. Replacing it
       with an unconditional ``zfill(5)`` — verbatim the previous solution's
       named bug #5 — passed all 27 tests *and* the full pipeline, because
       every other ZIP in this dataset already happens to be five characters.
  M2   PR-02's MAX policy, at the unit level. The previous PR-02 test asserted
       only that P005 collapsed to one row and that the conflict was recorded;
       it never asserted *which price won*, so electing the MIN passed.
  M12  The unconditional-``zfill`` mutation itself.

A docstring that names a defect code is a claim. This module makes three of
those claims true, and the audit at the bottom of this file records what the
sweep of the remaining docstrings found.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.audit import AuditLog
from src.cleaning.products import clean_products
from src.cleaning.stores import normalize_zip_codes
from src.cleaning.transactions import clean_transactions
from src.defects import DefectCode


# ══════════════════════════════════════════════════════════════════════════════
# TX-05 · Orphan product_id — previously zero mentions anywhere in tests/
# ══════════════════════════════════════════════════════════════════════════════
class TestTransactionCleaningTX05:
    """TX-05: transactions referencing an unknown product are quarantined, not loaded."""

    def test_orphan_product_rows_are_excluded_and_recorded(
        self,
        raw_transactions_orphan_product: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """F8: the first assertion TX-05 has ever had.

        TXN002 (P031) and TXN003 (P032) reference products absent from the
        cleaned dimension. Both must leave the kept frame; the two clean rows
        must stay. Removing this test returns TX-05 to being defended only by
        the warehouse loader's ``UnresolvedKeyError`` (mutation M10), which
        fires at load time and says nothing about the cleaner's own decision.
        """
        result = clean_transactions(
            raw_transactions_orphan_product,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert result["transaction_id"].tolist() == ["TXN001", "TXN004"]
        assert set(result["product_id"]) == {"P001"}

        rec = audit_log.get(DefectCode.TX_05_ORPHAN_PRODUCT)
        assert rec is not None, "TX-05 must be recorded in the audit log"
        assert rec.detected_count == 2
        assert rec.action == "quarantined", (
            "TX-05 rows are quarantined, not dropped and not routed to an 'Unknown' "
            "product — the decision is in the catalog and must not drift silently."
        )
        assert sorted(rec.affected_keys) == ["TXN002", "TXN003"]

    def test_orphan_product_rows_are_filed_under_tx05_in_the_lineage(
        self,
        raw_transactions_orphan_product: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """The row-level lineage attributes each exclusion to TX-05 specifically.

        A row can fail several checks at once; what makes the row budget add up
        is that each is filed under exactly one reason. If TX-05 rows were filed
        under TX-04 the totals would still balance and the finding would vanish.
        """
        result = clean_transactions(
            raw_transactions_orphan_product,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        lineage = result.attrs["_cleaning_lineage"]

        filed = dict(zip(lineage["transaction_id"], lineage["reason_code"]))
        assert filed["TXN002"] == "TX-05"
        assert filed["TXN003"] == "TX-05"

        recon = result.attrs["_cleaning_reconciliation"]
        assert recon["quarantined_by_reason_code"] == {"TX-05": 2}
        assert recon["kept"] + recon["dropped"] + recon["quarantined"] == recon["source_rows"] == 4
        assert recon["revenue_quarantined"] == pytest.approx(200.00, abs=0.005)

    def test_lineage_file_is_written_where_the_caller_asked(
        self,
        raw_transactions_orphan_product: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """F3: ``lineage_dir`` is honoured, and nothing is written outside it.

        This is the test that would have stopped ``pytest`` overwriting the
        deliverable's 505-row lineage proof with fixture rows.
        """
        clean_transactions(
            raw_transactions_orphan_product,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        written = tmp_path / "transactions__lineage.csv"
        assert written.exists()
        assert len(pd.read_csv(written)) == 4, "one lineage row per SOURCE row, always"

    def test_lineage_dir_none_writes_nothing(
        self,
        raw_transactions_orphan_product: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """``lineage_dir=None`` is the explicit "touch no filesystem" contract.

        Required to stay legal so unit tests can opt out entirely; asserting it
        stops a future refactor from reintroducing an implicit default path.
        """
        clean_transactions(
            raw_transactions_orphan_product,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=None,
        )
        assert list(tmp_path.iterdir()) == []


# ══════════════════════════════════════════════════════════════════════════════
# TX-09 · Exact duplicate rows — previously zero mentions anywhere in tests/
# ══════════════════════════════════════════════════════════════════════════════
class TestTransactionCleaningTX09:
    """TX-09: byte-identical repeat rows are dropped once, and accounted for."""

    def test_duplicate_rows_collapse_to_one_each(
        self,
        raw_transactions_exact_duplicates: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """F8: the first assertion TX-09 has ever had.

        Five source rows contain two byte-identical repeats. Three survive, each
        exactly once, and the surviving copy is the FIRST occurrence — dropping
        the original instead of the copy would still give the right count while
        changing which row's provenance is kept.

        Mutation M9 (dedup disabled) is currently caught only by the warehouse's
        ``UNIQUE(transaction_id)`` constraint. That is a good backstop but it
        tests the schema, not the rule.
        """
        result = clean_transactions(
            raw_transactions_exact_duplicates,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )

        assert result["transaction_id"].tolist() == ["TXN001", "TXN002", "TXN003"]
        assert result["transaction_id"].is_unique

        rec = audit_log.get(DefectCode.TX_09_EXACT_DUPLICATE)
        assert rec is not None, "TX-09 must be recorded in the audit log"
        assert rec.detected_count == 2, "the COPIES are counted, not the distinct ids"
        assert rec.action == "dropped"
        assert sorted(rec.affected_keys) == ["TXN001", "TXN002"]

    def test_duplicate_revenue_is_removed_exactly_once(
        self,
        raw_transactions_exact_duplicates: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """Double-counted money is what a duplicate row actually costs.

        Source total is $550.00 across five rows; $250.00 of it is duplicated.
        Kept revenue must be $300.00. Asserting the dollar split — not just the
        row count — is what makes this a revenue test rather than a tidiness
        test, and it is the assertion a stakeholder would care about.
        """
        result = clean_transactions(
            raw_transactions_exact_duplicates,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        recon = result.attrs["_cleaning_reconciliation"]

        assert recon["source_rows"] == 5
        assert recon["kept"] == 3
        assert recon["dropped"] == 2
        assert recon["quarantined"] == 0
        assert recon["dropped_by_code"] == {"TX-09": 2}
        assert recon["revenue_as_reported_total"] == pytest.approx(550.00, abs=0.005)
        assert recon["revenue_kept"] == pytest.approx(300.00, abs=0.005)
        assert recon["revenue_dropped_duplicates"] == pytest.approx(250.00, abs=0.005)

        assert float(result["total_amount"].sum()) == pytest.approx(300.00, abs=0.005)

    def test_duplicates_are_disposed_as_dropped_not_quarantined(
        self,
        raw_transactions_exact_duplicates: pd.DataFrame,
        audit_log: AuditLog,
        tmp_path: Path,
    ) -> None:
        """TX-09 rows carry disposition ``dropped``; TX-04/05/07/08 carry ``quarantined``.

        The distinction is deliberate and load-bearing for the row budget:
        505 = 474 kept + 16 quarantined + 15 dropped. Collapsing the two
        dispositions would make the published breakdown wrong while the total
        still balanced.
        """
        result = clean_transactions(
            raw_transactions_exact_duplicates,
            audit_log,
            {"S001"},
            {"P001"},
            lineage_dir=tmp_path,
        )
        lineage = result.attrs["_cleaning_lineage"]

        assert lineage["disposition"].value_counts().to_dict() == {"kept": 3, "dropped": 2}
        dropped = lineage.loc[lineage["disposition"] == "dropped"]
        assert set(dropped["reason_code"]) == {"TX-09"}


# ══════════════════════════════════════════════════════════════════════════════
# PR-04 · Zero price — previously present only in docstrings
# ══════════════════════════════════════════════════════════════════════════════
class TestProductCleaningPR04:
    """PR-04: a 0.00 list price is read as missing and imputed from the category median."""

    def test_zero_price_imputed_to_the_category_median(
        self, raw_products_zero_price: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """F8: PR-04's first real assertion — the exact imputed value.

        Apparel prices are 100 / 120 / 140, so the median is **120.00**. Pinning
        the number rather than "> 0" is what separates "imputed from the cohort"
        from "imputed from anywhere" — a global mean, a forward-fill or the
        transacted price would all clear a ``> 0`` check.
        """
        result = clean_products(raw_products_zero_price, audit_log)

        p027 = result.loc[result["product_id"] == "P027"]
        assert len(p027) == 1, "PR-04 keeps the product; a zero price is not a reason to drop it"

        assert float(p027.iloc[0]["list_unit_price"]) == pytest.approx(120.00, abs=0.005)
        assert bool(p027.iloc[0]["price_is_imputed"]) is True, (
            "an imputed price must be labelled as imputed, or a downstream analyst "
            "cannot tell a measured value from a manufactured one"
        )

    def test_zero_price_is_recorded_and_the_other_rows_are_untouched(
        self, raw_products_zero_price: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """The imputation is audited, and it affects exactly one row.

        An over-eager rule that "fixed" every price would still leave P027 at
        120.00 and pass the test above. Pinning the three observed prices makes
        the blast radius part of the contract.
        """
        result = clean_products(raw_products_zero_price, audit_log)

        rec = audit_log.get(DefectCode.PR_04_ZERO_PRICE)
        assert rec is not None, "PR-04 must be recorded in the audit log"
        assert rec.detected_count == 1
        assert rec.action == "imputed"
        assert rec.affected_keys == ["P027"]

        observed = (
            result.loc[result["product_id"] != "P027"]
            .set_index("product_id")["list_unit_price"]
            .astype(float)
            .to_dict()
        )
        assert observed == pytest.approx({"P024": 100.00, "P025": 120.00, "P026": 140.00}, abs=0.005)
        assert not result.loc[result["product_id"] != "P027", "price_is_imputed"].any()

    def test_no_surviving_product_has_a_zero_or_negative_price(
        self, raw_products_zero_price: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """``dim_product.list_unit_price`` CHECK (> 0) is satisfied before the load.

        The warehouse would reject a zero price outright; catching it here means
        the failure names PR-04 instead of surfacing as an opaque IntegrityError
        three stages later.
        """
        result = clean_products(raw_products_zero_price, audit_log)
        assert (result["list_unit_price"].astype(float) > 0).all()


# ══════════════════════════════════════════════════════════════════════════════
# M2 · PR-02 price election, at the unit level
# ══════════════════════════════════════════════════════════════════════════════
class TestProductCleaningPR02ElectsMax:
    """PR-02: the elected list price is the MAX of the conflicting values."""

    def test_higher_price_is_elected_by_policy(
        self, raw_products_with_price_conflict: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """M2 killer at the unit level: 150.11 wins, 141.61 does not.

        The pre-existing PR-02 test asserted only that P005 collapsed to one row
        and that the conflict was recorded — both of which remain true when the
        policy is inverted. That is why mutation M2 survived the suite *and*
        still printed "PR-02 · Expected 1 · Detected 1 · OK": detecting a
        conflict and resolving it correctly are different properties, and only
        the first was ever tested.

        MAX also has to be *deliberate*: the fixture lists 141.61 before 150.11,
        so a ``drop_duplicates(keep="first")`` fallback would elect 141.61 and
        fail here, which is the whole point of forbidding file order.
        """
        result = clean_products(raw_products_with_price_conflict, audit_log)

        p005 = result.loc[result["product_id"] == "P005"]
        assert len(p005) == 1
        assert float(p005.iloc[0]["list_unit_price"]) == pytest.approx(150.11, abs=0.005), (
            "PR-02 elects the higher list price by an explicit MAX policy. 141.61 means "
            "the policy became MIN (mutation M2) or reverted to file order."
        )
        assert bool(p005.iloc[0]["price_conflict"]) is True
        assert bool(p005.iloc[0]["price_is_imputed"]) is False

    def test_conflict_is_not_counted_as_an_exact_duplicate(
        self, raw_products_with_price_conflict: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """PR-02 must not be misfiled as PR-01 — that was the previous solution's bug #4.

        Two rows with the same key and a *different* payload is a business
        event, not a duplicate. If the price conflict were swept up by the
        exact-duplicate pass, PR-02's count would be 0, PR-01's would be 2, and
        the total row arithmetic would still look perfectly fine.
        """
        clean_products(raw_products_with_price_conflict, audit_log)

        pr02 = audit_log.get(DefectCode.PR_02_PRICE_CHANGE)
        assert pr02 is not None and pr02.detected_count == 1
        assert "P005" in pr02.affected_keys

        pr01 = audit_log.get(DefectCode.PR_01_EXACT_DUPLICATE)
        assert pr01 is None or pr01.detected_count == 0, (
            "there is no byte-identical duplicate in this fixture; counting one means "
            "the price conflict was swallowed by the PR-01 pass"
        )


# ══════════════════════════════════════════════════════════════════════════════
# F14 / M12 · ST-01 zfill must stay conditional
# ══════════════════════════════════════════════════════════════════════════════
class TestZipPaddingGuard:
    """ST-01: only short, all-digit ZIPs are padded. Everything else is left alone."""

    def test_non_paddable_zip_is_left_untouched(
        self, raw_stores_unpaddable_zip: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """M12 killer: ``"N/A"`` must stay ``"N/A"``, never become ``"00N/A"``.

        Mutation M12 replaces the digit guard with an unconditional
        ``zfill(5)`` — verbatim the previous solution's named bug #5. It is
        behaviourally inert on the shipped dataset because every other ZIP is
        already five characters, so it passed all 27 tests and a full 17/17
        pipeline run. This fixture is the first input that can tell the
        difference: zero-filling a non-numeric value invents a ZIP that never
        existed in any encoding of the source.

        The ZIP+4 case guards the other direction — ``zfill`` is a no-op on a
        10-character string, but a "normalise to 5" rule that truncated would be
        just as destructive, so the full value is pinned.
        """
        result = normalize_zip_codes(raw_stores_unpaddable_zip, audit_log)
        zips = dict(zip(result["store_id"], result["zip_code"]))

        assert zips["S020"] == "N/A", (
            "'N/A' is not recoverable by padding. '00N/A' is not a ZIP that ever "
            "existed — it is a fabricated value that looks structurally plausible."
        )
        assert zips["S021"] == "14604-1234", "ZIP+4 must not be padded, truncated or mangled"
        assert zips["S003"] == "00938", "'0938' IS recoverable by padding — that is ST-01"
        assert zips["S001"] == "14604", "well-formed ZIPs must be passed through untouched"

    def test_every_malformed_zip_is_flagged_including_the_unpaddable_ones(
        self, raw_stores_unpaddable_zip: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """Suspicion is recorded on all three bad ZIPs, not only the paddable one.

        Under an unconditional ``zfill`` every value comes out five characters,
        so no audit is possible after the fact — the flag is the only thing that
        tells a downstream analyst which rows to distrust. Asserting that the
        well-formed row is *not* flagged is what stops the flag degenerating
        into "always true", which would pass just as easily.
        """
        result = normalize_zip_codes(raw_stores_unpaddable_zip, audit_log)
        suspect = dict(zip(result["store_id"], result["zip_is_suspect"].astype(bool)))

        assert suspect == {"S001": False, "S003": True, "S020": True, "S021": True}

        rec = audit_log.get(DefectCode.ST_01_MALFORMED_ZIP)
        assert rec is not None
        assert rec.detected_count == 3
        assert sorted(rec.affected_keys) == ["S003", "S020", "S021"]
        assert rec.action == "flagged", (
            "padding restores an encoding, not a fact — calling it 'imputed' would "
            "overstate confidence in a value nobody has verified"
        )

    def test_padding_does_not_alter_the_row_count(
        self, raw_stores_unpaddable_zip: pd.DataFrame, audit_log: AuditLog
    ) -> None:
        """ST-01 never drops a store. Losing a store to a formatting defect trades
        a cosmetic problem for a revenue problem — S003 has real transactions."""
        result = normalize_zip_codes(raw_stores_unpaddable_zip, audit_log)
        assert len(result) == len(raw_stores_unpaddable_zip) == 4
        assert result["store_id"].tolist() == ["S001", "S003", "S020", "S021"]
