"""Transaction cleaning: ten defect classes, one stated decision each.

This is the heart of the pipeline. ``data/raw/transactions.csv`` holds 505 rows
and every one of them must leave this module accounted for -- kept, dropped or
quarantined -- with the arithmetic published so a reviewer can add it up.

Defect codes owned
------------------
============  ==========================================  =========  ============
Code          Problem                                     Expected   Decision
============  ==========================================  =========  ============
TX-01         Three date formats in one column            20         parse per format
TX-02         Amounts formatted as currency strings       25         parse explicitly
TX-03         Reported total != qty x unit_price          20         **preserve**
TX-04         store_id not in the store dimension          5         quarantine
TX-05         product_id not in the product dimension      3         quarantine
TX-06         NULL customer_id (guest checkout)           40         keep + sentinel
TX-07         quantity == 0 and total == 0                 5         quarantine
TX-08         transaction_date > AS_OF_DATE                3         quarantine
TX-09         Byte-identical duplicate rows               15         drop the copies
TX-10         Returns: negative quantity and amount       30         **preserve**
============  ==========================================  =========  ============

ORDER OF OPERATIONS -- and why it is what it is
-----------------------------------------------
The stage order below is not incidental. Three of these defects are *created* by
running the pipeline in the wrong order, so the sequence is part of the answer.

**Phase 1 -- DETECT everything against the 505-row source. Phase 2 -- DECIDE.**

That split is the top-level design choice. Every detection mask in this module
is computed against the full source frame *before* a single row is removed, and
every ``DefectRecord.detected_count`` is taken from those source-level masks.
The alternative -- detect as you go, on whatever survived the previous stage --
looks equivalent and is not: it makes the completeness proof in
``AuditLog.assert_all_expected_defects_found()`` compare detected counts drawn
from a 474-row population against expected counts drawn from a 505-row one.
Those numbers happen to agree on this file because ``seed_data.py`` uses
non-overlapping index ranges, but they would silently diverge on the next
extract, and a completeness check that is only accidentally correct is worse
than none. (Contract §7b ADDENDUM: *"detected_count counts source rows observed,
never post-filter survivors."*)

Within Phase 1 the stage order is forced by real dependencies:

1. **Whitespace normalisation first.** Trimming is a cleaning decision, not a
   read-time side effect (``read_csv_as_str`` deliberately does none), and every
   later comparison -- duplicate detection, null detection, key lookup -- is
   wrong if ``"S001"`` and ``"S001 "`` are different values.

2. **TX-09 duplicate detection second, on the RAW STRINGS, before any parsing.**
   This is the load-bearing ordering choice in the module. A duplicate must be
   defined as *byte-identical in the source*, because that is the only definition
   that reliably distinguishes a re-extract from two real events. Parse first and
   the definition rots: ``"$142.50"`` and ``"142.50"`` become the same float, so a
   formatting variant of a genuinely different record would be silently collapsed
   into a duplicate and a real transaction would vanish. Detecting on raw text
   cannot make that mistake. (Detection happens here; the actual removal happens
   in Phase 2, so the parse stages still see all 505 rows and their counts stay
   source-level.)

3. **TX-02 currency parsing before TX-03 reconciliation.** Obvious once stated:
   ``"$142.50" != 3 * 47.50`` compares a string to a float and is True for every
   row, so reconciling before coercing would report 25 phantom discounts and bury
   the 20 real ones.

4. **TX-01 date parsing before TX-08 future-date detection.** This is the exact
   ordering the previous solution got wrong, and it manufactured a false business
   finding. Its single ``pd.to_datetime(errors="coerce")`` NaT-ed the 20 non-ISO
   rows; the future-date check then ran on the wreckage and the README reported
   "20 future-dated transactions". The real number is **3**. A parser bug had
   become a business claim. Parsing first, with explicit formats and zero row
   loss, means TX-08 can only ever count rows that genuinely carry a date after
   ``AS_OF_DATE``.

5. **TX-04/TX-05 referential integrity after parsing, not before.** The keys
   themselves need no parsing, but the *cost* of the exclusion does: the audit
   note states how many dollars are being withheld from the warehouse, and that
   number does not exist until the amounts are floats.

6. **TX-07 zero-quantity detection before TX-03 is interpreted.** ``0 == 0 * p``
   is trivially true, so zero-quantity rows can never be discounts; they are
   excluded from the reconciliation population so they cannot dilute the
   discount statistics with rows that carry no economics at all.

Phase 2 then applies the decisions exactly once, in a single pass, with a stated
precedence for rows that qualify under more than one code (none do in this file,
and the module proves it rather than assuming it).

Inputs:  an all-string DataFrame from :func:`src.io_utils.read_csv_as_str`, the
         shared :class:`~src.audit.AuditLog`, and the surviving key sets from the
         already-cleaned store and product dimensions.
Outputs: the cleaned fact frame (columns fixed by contract §4), a full row-level
         lineage frame at ``output/quarantine/transactions__lineage.csv``, one
         quarantine CSV per excluded defect code, and audit records for all ten
         codes.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Final

import pandas as pd

from src.audit import AuditLog, DefectRecord
from src.config import AS_OF_DATE, GUEST_CUSTOMER_ID, PRICE_TOLERANCE, QUARANTINE_DIR
from src.defects import DefectCode
from src.io_utils import write_dataframe_csv
from src.cleaning.rules import (
    coerce_int_series,
    is_blank,
    normalise_text,
    parse_currency_series,
    parse_date_series,
    reconcile_line_amount,
    round_money,
)

# ── Contract-fixed shapes ─────────────────────────────────────────────────────
SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "transaction_id",
    "transaction_date",
    "store_id",
    "product_id",
    "customer_id",
    "quantity",
    "unit_price",
    "total_amount",
)
"""The eight columns ``data/raw/transactions.csv`` actually has. Checked on entry:
WHY fail loudly on a missing column rather than let a KeyError surface 200 lines
later -- an upstream schema change should be reported as a schema change."""

OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    "transaction_id",
    "transaction_date",
    "store_id",
    "product_id",
    "customer_id",
    "is_guest",
    "quantity",
    "unit_price",
    "extended_amount",
    "total_amount",
    "discount_amount",
    "has_discount",
    "is_return",
)
"""Contract §4, binding. ``src/warehouse/loader.py`` selects by name from this
list, so the *set* is a hard interface and the order is the documented one."""

LINEAGE_FILENAME: Final[str] = "transactions__lineage.csv"
"""Row-level disposition for all 505 source rows, written next to the quarantine
CSVs. This is the artefact that makes the completeness claim checkable by hand
rather than merely asserted in a log line."""

# ── Internal working-column prefix ────────────────────────────────────────────
# WHY a prefix convention: this module adds ~20 scratch columns to the working
# frame. Prefixing them makes the final projection a filter rather than a
# hand-maintained drop list, so a new scratch column can never leak into
# fact_sales because somebody forgot to delete it.
_WORK: Final[str] = "_wk_"


# ══════════════════════════════════════════════════════════════════════════════
# Phase 0 · Entry validation and normalisation
# ══════════════════════════════════════════════════════════════════════════════
def _validate_source_shape(df: pd.DataFrame) -> None:
    """Fail fast if the source frame is not the shape the module was written for.

    Args:
        df: Raw transactions frame.

    Raises:
        ValueError: If a contract column is missing.

    Defects handled: none (guard).
    """
    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"transactions.csv is missing required column(s) {missing}. "
            f"Present: {list(df.columns)}. Refusing to clean a frame of unknown shape."
        )


def _normalise_source(df: pd.DataFrame) -> pd.DataFrame:
    """Trim whitespace on every source column and stamp a stable row identity.

    WHY a positional ``_wk_source_row`` rather than trusting the DataFrame index:
    the index of a frame that has been concatenated, filtered and re-sliced is
    not a reliable identity, and the lineage file's whole job is to let a
    reviewer point at row 137 of the CSV. Position from the top of the file is
    the only identity the source actually offers.

    Args:
        df: Raw transactions frame.

    Returns:
        A copy with normalised text, a ``_wk_source_row`` column and a
        ``_wk_raw_signature`` column holding the pre-parse row fingerprint.

    Defects handled: none directly -- but TX-06's null detection and TX-09's
        byte-identity test both depend on this normalisation having happened.
    """
    work = df.copy()
    for column in SOURCE_COLUMNS:
        # WHY map(normalise_text) rather than .str.strip(): .str.strip() leaves
        # a whitespace-only cell as "" while leaving a true NaN as NaN, so the
        # column ends up with two different spellings of "missing" and every
        # later .isna() check is quietly incomplete.
        work[column] = work[column].map(normalise_text)

    work[f"{_WORK}source_row"] = range(len(work))
    # WHY build the signature here, before any parsing: see the module header,
    # ordering note 2. This string IS the definition of "exact duplicate", and it
    # must be taken from the source text, not from parsed values.
    work[f"{_WORK}raw_signature"] = (
        work[list(SOURCE_COLUMNS)].astype(object).where(work[list(SOURCE_COLUMNS)].notna(), "\x00")
        .astype(str)
        .agg("\x1f".join, axis=1)
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-09 · Exact duplicate rows
# ══════════════════════════════════════════════════════════════════════════════
def drop_exact_duplicates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Detect byte-identical duplicate rows, and separately probe for ID collisions.

    ── TX-09 · Exact duplicate rows ──────────────────────────────────────────
    WHY: ``seed_data.py`` line 197 appends ``df.iloc[50:65]`` -- 15 verbatim
    copies of TXN10051..TXN10065, same IDs, same measures, same dates. Left in,
    they double-count roughly 3% of revenue, and because they are a contiguous
    ID block the damage lands on specific stores and products rather than
    averaging out into the noise.
    DECISION: keep the first occurrence, mark the later copies for removal.
    De-duplication is keyed on the **entire raw row**, not on ``transaction_id``.
    ALTERNATIVE REJECTED: ``drop_duplicates(subset=["transaction_id"])``. On this
    file it produces the same 490 rows, so it looks equally good -- and it is the
    trap. A key-only rule cannot tell a re-extract (identical payload, safe to
    collapse) from a collision (same ID, *different* amounts, which is an
    unresolved conflict and possibly two real events). It would silently keep
    whichever row happened to sort first and destroy the evidence, which is
    exactly how PR-02's price change disappeared from the previous attempt.

    So this function runs the generic check as well: how many ``transaction_id``
    values repeat *without* the rows being identical? The expected answer for
    this file is zero, and proving that zero is the point -- an unasserted "there
    are no collisions" is an assumption, an asserted one is a finding.

    Args:
        df: Normalised working frame (all 505 rows).
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_is_exact_duplicate`` and ``_wk_is_id_collision``
        boolean columns added. **Nothing is removed here** -- removal happens in
        Phase 2 so that every other detector still sees the full source
        population and its counts stay source-level.

    Defects handled: TX-09.
    """
    work = df.copy()

    # DEFECT: TX-09
    is_duplicate = work[f"{_WORK}raw_signature"].duplicated(keep="first")
    work[f"{_WORK}is_exact_duplicate"] = is_duplicate

    # ── The generic collision probe ──────────────────────────────────────────
    # A transaction_id that repeats while the rows are NOT byte-identical is the
    # transaction-level analogue of PR-02: a key collision carrying conflicting
    # payloads. It must never be resolved by dropping one side.
    id_repeats = work["transaction_id"].duplicated(keep=False)
    signature_repeats = work[f"{_WORK}raw_signature"].duplicated(keep=False)
    collisions = id_repeats & ~signature_repeats
    work[f"{_WORK}is_id_collision"] = collisions
    collision_ids = sorted(work.loc[collisions, "transaction_id"].dropna().unique().tolist())

    duplicate_ids = work.loc[is_duplicate, "transaction_id"].tolist()
    id_span = f"{min(duplicate_ids)}..{max(duplicate_ids)}" if duplicate_ids else "n/a"
    audit.record(
        DefectRecord(
            code=DefectCode.TX_09_EXACT_DUPLICATE,
            detected_count=int(is_duplicate.sum()),
            action="dropped",
            affected_keys=duplicate_ids,
            notes=(
                f"{int(is_duplicate.sum())} rows are byte-identical repeats of an earlier row "
                f"(contiguous ID block {id_span}), de-duplicated on the full raw row rather "
                f"than on transaction_id alone. Generic same-ID/different-payload collision "
                f"check: {len(collision_ids)} found"
                + (f" ({', '.join(collision_ids[:10])})" if collision_ids else "")
                + " -- so every repeated ID in this file is a true re-extract, not a conflict, "
                "and collapsing them cannot lose information. The dropped copies are written to "
                "output/quarantine/transactions__TX-09.csv so the row arithmetic still ties "
                "back to 505."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-01 · Mixed date formats
# ══════════════════════════════════════════════════════════════════════════════
def parse_transaction_dates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Parse ``transaction_date`` against the ordered format ladder.

    ── TX-01 · Three date formats in one column ──────────────────────────────
    WHY: ``seed_data.py`` lines 160-165 rewrite rows 0-9 as ``MM/DD/YYYY`` and
    rows 10-19 as ``DD-MM-YYYY``; the remaining 485 stay ISO ``YYYY-MM-DD``.
    DECISION: attempt each format in ``config.DATE_FORMATS`` order, record which
    one matched, and assert that **zero rows** fail every rung. All 20 non-ISO
    rows are recovered and none is lost.
    ALTERNATIVE REJECTED: ``pd.to_datetime(col, errors="coerce")``. It fails in
    one of two ways and both are silent. Either it NaTs the 20 non-ISO rows --
    deleting real transactions and real revenue -- or, if pandas settles on a
    single dayfirst guess for the whole column, it *misparses* them: ``03-05-2026``
    becomes 5 March when the source meant 3 May. The second is worse, because the
    output is complete and plausible and wrong. The previous attempt hit the
    first mode, lost all 20 rows, and then reported them in its README as
    "future-dated" -- turning a parser bug into a fictitious business finding
    (the real future-dated count is 3; see :func:`flag_future_dates`).

    On the ambiguity, explicitly. For any of these strings whose leading
    component is <= 12, the day/month reading is **not** determined by the string:
    ``"03-05-2026"`` is a perfectly valid 3 May *and* a perfectly valid 5 March,
    and ``"05/03/2026"`` has the same problem mirrored. Nothing in the value
    itself breaks the tie. What breaks it here is structure plus provenance:

    * a four-digit leading component can be neither a day nor a month, so ISO is
      self-identifying and is attempted first;
    * ``'/'`` appears only in the US rewrite and ``'-'`` with a two-digit head
      only in the EU rewrite, so the separator carries the provenance;
    * therefore no string can match two rungs, and the tuple order in
      ``config.DATE_FORMATS`` is a stated tie-break rule rather than a hope.

    The parser additionally counts how many rows would have parsed to a
    *different* valid date under the swapped reading, and publishes that count in
    the audit note. That is the honest disclosure: those rows are resolved by
    provenance, not by evidence in the data, and a reader is entitled to know how
    many of them there are.

    Args:
        df: Working frame with ``_wk_`` scratch columns.
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_transaction_date`` (datetime64), the matched format,
        and ``_wk_date_unparsed`` added.

    Defects handled: TX-01.
    """
    work = df.copy()
    # DEFECT: TX-01
    parsed = parse_date_series(work["transaction_date"])

    work[f"{_WORK}transaction_date"] = parsed["parsed_date"]
    work[f"{_WORK}date_format"] = parsed["matched_format"]
    work[f"{_WORK}date_needed_non_iso"] = parsed["needed_non_iso_format"]
    work[f"{_WORK}date_ambiguous"] = parsed["is_ambiguous"]
    # WHY "blank OR unmatched" and not just "unmatched": a missing date and an
    # unreadable date are both unusable, and both must be quarantined rather than
    # coerced -- but only after we have counted them, never by a coerce call.
    work[f"{_WORK}date_unparsed"] = parsed["parsed_date"].isna()

    non_iso_mask = parsed["needed_non_iso_format"].fillna(False).astype(bool)
    by_format = (
        work.loc[non_iso_mask, f"{_WORK}date_format"].value_counts().sort_index().to_dict()
    )
    full_distribution = work[f"{_WORK}date_format"].value_counts(dropna=False).to_dict()
    ambiguous_count = int((parsed["is_ambiguous"] & non_iso_mask).sum())
    unparsed_count = int(work[f"{_WORK}date_unparsed"].sum())

    audit.record(
        DefectRecord(
            code=DefectCode.TX_01_MIXED_DATE_FORMATS,
            detected_count=int(non_iso_mask.sum()),
            action="imputed",
            affected_keys=work.loc[non_iso_mask, "transaction_id"].tolist(),
            notes=(
                f"Format distribution across all {len(work)} source rows: "
                f"{ {str(k): int(v) for k, v in full_distribution.items()} }. "
                f"Non-ISO rows recovered by explicit format: "
                f"{ {str(k): int(v) for k, v in by_format.items()} }. "
                f"Rows lost to parsing: {unparsed_count} (a single "
                "pd.to_datetime(errors='coerce') would have lost or misparsed all "
                f"{int(non_iso_mask.sum())}). Genuine day/month ambiguity: {ambiguous_count} of "
                f"{int(non_iso_mask.sum())} non-ISO rows have a leading component <= 12, so the "
                "swapped reading is also a valid calendar date; these are resolved by separator "
                "provenance (seed_data.py:160-165), not by evidence in the string itself. The "
                "remaining rows are structurally forced because their leading component exceeds "
                "12 and therefore cannot be a month."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-02 · Currency-formatted amounts
# ══════════════════════════════════════════════════════════════════════════════
def parse_amounts(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Coerce ``quantity``, ``unit_price`` and ``total_amount`` explicitly.

    ── TX-02 · Amounts stored as currency strings ────────────────────────────
    WHY: ``seed_data.py`` lines 169-170 rewrite rows 20-44 as ``"$392.40"``.
    Because ``read_csv_as_str`` reads everything as text, the defect survives to
    be counted; with pandas' default inference ``total_amount`` would arrive as
    an ``object`` column holding 480 floats and 25 strings, and ``.sum()`` would
    either raise or concatenate depending on the version.
    DECISION: strip the presentation characters, parse to float, and quarantine
    anything that still refuses to parse.
    ALTERNATIVE REJECTED: ``pd.to_numeric(errors="coerce").fillna(0)``. It
    produces a beautifully clean float column that no schema test would flag,
    while silently zeroing ~$3.5k of revenue. Losing money is bad; losing it
    invisibly is the failure mode this whole project exists to demonstrate
    against. ``None`` forces a decision; ``0.0`` forecloses one.

    ``quantity`` is coerced here too, into a **nullable** ``Int64``, so that a
    failed parse stays distinguishable from a legitimate ``0`` -- which matters
    because a legitimate ``0`` is a defect in its own right (TX-07).

    Args:
        df: Working frame.
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_quantity`` (Int64), ``_wk_unit_price`` (float),
        ``_wk_total_amount`` (float) and ``_wk_amount_unparsed`` added.

    Defects handled: TX-02 (and the mechanical half of TX-07 and TX-10).
    """
    work = df.copy()

    # DEFECT: TX-02
    totals = parse_currency_series(work["total_amount"])
    prices = parse_currency_series(work["unit_price"])
    quantities = coerce_int_series(work["quantity"])

    work[f"{_WORK}total_amount"] = totals["amount"]
    work[f"{_WORK}unit_price"] = prices["amount"]
    work[f"{_WORK}quantity"] = quantities["value"]

    # WHY one combined "unusable measures" flag: a row missing any one of the
    # three cannot participate in revenue, reconciliation or the fact grain. Three
    # separate quarantine buckets would fragment the evidence for no benefit.
    work[f"{_WORK}amount_unparsed"] = (
        totals["amount"].isna() | prices["amount"].isna() | quantities["value"].isna()
    )

    currency_formatted = totals["was_currency_formatted"]
    example = work.loc[currency_formatted, "total_amount"].head(1).tolist()
    recovered = round_money(float(totals.loc[currency_formatted, "amount"].sum()))
    audit.record(
        DefectRecord(
            code=DefectCode.TX_02_STRING_CURRENCY,
            detected_count=int(currency_formatted.sum()),
            action="imputed",
            affected_keys=work.loc[currency_formatted, "transaction_id"].tolist(),
            notes=(
                f"{int(currency_formatted.sum())} total_amount values are not bare decimals "
                f"(e.g. {example[0] if example else 'n/a'}); "
                f"${recovered:,.2f} of revenue is carried by them and would have been zeroed by "
                "pd.to_numeric(errors='coerce').fillna(0). Rows where an amount or quantity "
                f"still failed to parse after normalisation: "
                f"{int(work[f'{_WORK}amount_unparsed'].sum())} (quarantined, never coerced to "
                "zero). unit_price and quantity are coerced in the same pass so that TX-03's "
                "reconciliation compares numbers with numbers rather than a float with a string."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-06 · NULL customer_id (guest checkouts)
# ══════════════════════════════════════════════════════════════════════════════
def handle_guest_customers(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Replace NULL ``customer_id`` with the GUEST sentinel; keep every row.

    ── TX-06 · NULL customer_id ──────────────────────────────────────────────
    WHY: ``seed_data.py`` line 187 nulls ``customer_id`` on rows 200-239. Forty
    rows, just under 8% of the file. This is **not corruption**: it is a guest
    checkout, an ordinary retail event that the schema simply has no flag for.
    DECISION: keep every row, write ``config.GUEST_CUSTOMER_ID`` into
    ``customer_id`` and set ``is_guest = True``. ``dim_customer`` then carries a
    single GUEST member and ``fact_sales`` gets its non-null foreign key.
    ALTERNATIVE REJECTED (1): dropping the rows -- the obvious "clean the nulls"
    move, and it deletes ~8% of real revenue while skewing every store, category
    and regional figure downwards by an amount no reconciliation would surface.
    ALTERNATIVE REJECTED (2): keeping the NULL -- ``dim_customer`` needs a natural
    key and ``fact_sales`` needs a non-null FK, so a NULL would fail the load.

    The sentinel has one consequence that must be stated rather than hidden: the
    GUEST member is a fused pseudo-person made of 40 unrelated shoppers, so it
    would top any lifetime-value ranking by construction. ``is_guest`` is what
    lets ``top_customers_lifetime`` exclude it in a documented
    ``definition_note`` instead of a silent ``WHERE`` clause, while every revenue
    metric still counts the money.

    Args:
        df: Working frame.
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_customer_id`` and ``_wk_is_guest`` added.

    Defects handled: TX-06.
    """
    work = df.copy()
    is_guest = work["customer_id"].map(is_blank).astype(bool)

    # DEFECT: TX-06
    work[f"{_WORK}customer_id"] = work["customer_id"].where(~is_guest, GUEST_CUSTOMER_ID)
    work[f"{_WORK}is_guest"] = is_guest

    guest_revenue = round_money(float(work.loc[is_guest, f"{_WORK}total_amount"].sum()))
    share = (is_guest.sum() / len(work) * 100) if len(work) else 0.0
    audit.record(
        DefectRecord(
            code=DefectCode.TX_06_NULL_CUSTOMER,
            detected_count=int(is_guest.sum()),
            action="imputed",
            affected_keys=work.loc[is_guest, "transaction_id"].tolist(),
            notes=(
                f"{int(is_guest.sum())} rows ({share:.1f}% of the file) carry no customer_id. "
                f"All are KEPT with customer_id='{GUEST_CUSTOMER_ID}' and is_guest=True; they "
                f"represent ${guest_revenue:,.2f} of revenue that dropping the nulls would have "
                "deleted outright. GUEST is one dimension member fused from many shoppers, so "
                "it is excluded from top_customers_lifetime (stated in that metric's "
                "definition_note) and included in every other metric."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-10 · Return transactions
# ══════════════════════════════════════════════════════════════════════════════
def flag_returns(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Flag negative-quantity, negative-amount rows as returns and keep them.

    ── TX-10 · Returns with negative quantity and amount ─────────────────────
    WHY: ``seed_data.py`` lines 200-203 copy base rows 65-94, negate both
    measures and re-key them to TXN20001..TXN20030. Thirty credit-side rows.
    DECISION: preserve them in the fact table with their signs intact and set
    ``is_return = True``. Detection is on the **sign of the measures**, not on
    the ID prefix -- a rule keyed to ``TXN2*`` would work on this file and break
    on the next extract, and it would describe the seed script rather than the
    business event. The ID block is used only to corroborate the finding in the
    audit note.
    ALTERNATIVE REJECTED (1): filtering them out as "invalid negatives" -- the
    classic sanity-check-shaped bug. It overstates net revenue by the full value
    of the returns and makes a return rate impossible to compute at all.
    ALTERNATIVE REJECTED (2): ``abs()`` on the measures -- worse by exactly a
    factor of two, because it converts every refund into a sale.

    **Modelling limitation, stated because it is real:** these rows carry no link
    back to the sale they reverse. ``seed_data.py`` copies rows 65-94 and issues
    new IDs, so the relationship exists in the generator and is simply absent
    from the data. Consequently the return rate can only ever be computed as an
    aggregate ratio (returned units over sold units in a period), never as a true
    per-sale return, and a return can be attributed to the period it was
    *recorded* in rather than the period the original sale fell in -- which will
    distort any month-over-month return figure near a period boundary. The
    correct fix is an ``original_transaction_id`` column in the source; nothing in
    this pipeline can recover it, and pretending otherwise by matching on
    store/product/amount would fabricate links that may not exist.

    Args:
        df: Working frame.
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_is_return`` added.

    Defects handled: TX-10.
    """
    work = df.copy()
    quantity = work[f"{_WORK}quantity"]
    total = work[f"{_WORK}total_amount"]

    # DEFECT: TX-10
    is_return = ((quantity < 0) & (total < 0)).fillna(False).astype(bool)
    work[f"{_WORK}is_return"] = is_return

    # WHY assert the two signs agree rather than test only one: a row with a
    # negative quantity and a POSITIVE amount is not a return, it is an
    # unexplained anomaly, and quietly folding it in here would hide it.
    sign_disagreement = int(
        (((quantity < 0) & (total >= 0)) | ((quantity >= 0) & (total < 0))).fillna(False).sum()
    )
    return_ids = sorted(work.loc[is_return, "transaction_id"].dropna().tolist())
    id_span = f"{return_ids[0]}..{return_ids[-1]}" if return_ids else "n/a"
    audit.record(
        DefectRecord(
            code=DefectCode.TX_10_RETURNS,
            detected_count=int(is_return.sum()),
            action="preserved",
            affected_keys=return_ids,
            notes=(
                f"{int(is_return.sum())} rows carry negative quantity AND negative total "
                f"(ID block {id_span}), preserved with signs intact so SUM(total_amount) is "
                f"genuinely net: they offset ${abs(round_money(float(total[is_return].sum()))):,.2f} "
                f"and {abs(int(quantity[is_return].sum()))} units. Rows where the two signs "
                f"disagree (negative quantity with positive amount or vice versa): "
                f"{sign_disagreement} -- such rows would be an anomaly, not a return, and are "
                "deliberately not absorbed into this class. MODELLING LIMITATION: the source "
                "carries no link from a return to the sale it reverses, so return rate is only "
                "computable as an aggregate ratio and returns are attributed to the period they "
                "were recorded in, not the period of the original sale."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-07 · Zero-quantity rows
# ══════════════════════════════════════════════════════════════════════════════
def flag_zero_quantity(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Flag ``quantity == 0`` rows for quarantine.

    ── TX-07 · Zero-quantity, zero-value transactions ────────────────────────
    WHY: ``seed_data.py`` line 190 sets ``quantity`` and ``total_amount`` to 0 on
    rows 250-254. A sale of zero units for zero dollars is not a sale; it is a
    voided line, an abandoned order stub or a system artefact.
    DECISION: quarantine and exclude from ``fact_sales``.
    ALTERNATIVE REJECTED: leaving them in because "they contribute zero revenue
    so they are harmless". They are not harmless -- they are rows in the
    *denominator*. Average order value divides revenue by transaction count, so
    five economically empty rows push AOV down by about 1% for no economic reason
    at all: a metric moved by rows that represent nothing. Removing them changes
    no total and repairs a denominator.

    The two conditions are checked **separately** rather than as one combined
    test: a zero-quantity row carrying money would be a different and more
    alarming anomaly, and this function reports the count rather than absorbing
    it. Zero-quantity rows are also excluded from the TX-03 reconciliation
    population (see :func:`reconcile_totals`), where ``0 == 0 * price`` is
    trivially true and would dilute the discount statistics.

    Args:
        df: Working frame.
        audit: Ledger to record into.

    Returns:
        The frame with ``_wk_is_zero_quantity`` added.

    Defects handled: TX-07.
    """
    work = df.copy()
    quantity = work[f"{_WORK}quantity"]
    total = work[f"{_WORK}total_amount"]

    # DEFECT: TX-07
    is_zero_quantity = (quantity == 0).fillna(False).astype(bool)
    work[f"{_WORK}is_zero_quantity"] = is_zero_quantity

    zero_qty_with_money = int((is_zero_quantity & (total.abs() > PRICE_TOLERANCE)).sum())
    audit.record(
        DefectRecord(
            code=DefectCode.TX_07_ZERO_QUANTITY,
            detected_count=int(is_zero_quantity.sum()),
            action="quarantined",
            affected_keys=work.loc[is_zero_quantity, "transaction_id"].tolist(),
            notes=(
                f"{int(is_zero_quantity.sum())} rows have quantity == 0. Of those, "
                f"{zero_qty_with_money} also carry a non-zero total_amount -- checked separately "
                "because a zero-quantity row with money on it would be a distinct anomaly rather "
                "than a voided line. Excluded from fact_sales so they cannot inflate the AOV "
                "denominator (~1% understatement if retained), and excluded from the TX-03 "
                "reconciliation population where 0 == 0 * price is trivially true. Revenue "
                "impact of the exclusion: $0.00."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-08 · Future-dated transactions
# ══════════════════════════════════════════════════════════════════════════════
def flag_future_dates(
    df: pd.DataFrame,
    audit: AuditLog,
    as_of_date: dt.date = AS_OF_DATE,
) -> pd.DataFrame:
    """Flag transactions dated after the reference date for quarantine.

    ── TX-08 · Transactions dated after AS_OF_DATE ───────────────────────────
    WHY: ``seed_data.py`` lines 193-194 plant exactly **three** rows at
    ``TODAY + 8``, ``+16`` and ``+25`` days, where the generator's TODAY is
    2026-06-02. A sale cannot be recorded before it happens, so these are
    data-entry or timezone errors, or pre-orders the schema cannot represent.
    Either way they leak revenue into a period that has not closed, which is how
    a month-end reconciliation quietly stops tying out.
    DECISION: quarantine and exclude, comparing against ``config.AS_OF_DATE``.
    ALTERNATIVE REJECTED (1): comparing against ``datetime.now()``. The wall clock
    is already past 2026-06-02, so those three rows would silently become
    ordinary history and this defect would report zero -- while every
    trailing-30-day metric simultaneously went empty because the newest
    transaction is older than 30 real days. Pinning the reference date is what
    makes the run reproducible *and* what keeps this count at 3 forever.
    ALTERNATIVE REJECTED (2): keeping them with a flag. Rejected for consistency
    with TX-04/TX-05/TX-07: rows that cannot be trusted in the fact table are
    quarantined, and the same structural problem gets the same treatment.

    **THE REAL COUNT IS 3.** This is worth stating loudly because the previous
    solution reported 20. Its date parser lost the TX-01 rows to a coerce call,
    and its future-date check then ran on the damage and attributed the loss
    here. Ordering is the defence: this function runs strictly *after*
    :func:`parse_transaction_dates`, which asserts zero rows lost to parsing, so
    a misparsed date cannot be misreported as a future date. A naive pipeline
    conflates the two and turns a parser bug into a business claim.

    Args:
        df: Working frame, already date-parsed.
        audit: Ledger to record into.
        as_of_date: Reference "today". Defaults to :data:`src.config.AS_OF_DATE`.

    Returns:
        The frame with ``_wk_is_future_dated`` added.

    Defects handled: TX-08.
    """
    work = df.copy()
    cutoff = pd.Timestamp(as_of_date)

    # DEFECT: TX-08
    is_future = (work[f"{_WORK}transaction_date"] > cutoff).fillna(False).astype(bool)
    work[f"{_WORK}is_future_dated"] = is_future

    withheld = round_money(float(work.loc[is_future, f"{_WORK}total_amount"].sum()))
    if is_future.any():
        offsets = (
            (work.loc[is_future, f"{_WORK}transaction_date"] - cutoff).dt.days.sort_values().tolist()
        )
    else:  # pragma: no cover - defensive
        offsets = []
    audit.record(
        DefectRecord(
            code=DefectCode.TX_08_FUTURE_DATE,
            detected_count=int(is_future.sum()),
            action="quarantined",
            affected_keys=work.loc[is_future, "transaction_id"].tolist(),
            notes=(
                f"{int(is_future.sum())} rows are dated after AS_OF_DATE={as_of_date.isoformat()} "
                f"by {offsets} days, withholding ${withheld:,.2f} from fact_sales. The "
                "comparison uses the configured reference date, never datetime.now(): against "
                "the wall clock these rows would already be history and this defect would report "
                "zero. This check runs AFTER TX-01 date parsing (which lost 0 rows), so a "
                "misparsed date cannot be misreported here -- the previous solution reported 20 "
                "future-dated rows, which were in fact its own 20 coerce-mangled TX-01 rows."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-04 / TX-05 · Referential integrity
# ══════════════════════════════════════════════════════════════════════════════
def check_referential_integrity(
    df: pd.DataFrame,
    audit: AuditLog,
    valid_store_ids: set[str],
    valid_product_ids: set[str],
) -> pd.DataFrame:
    """Flag transactions whose store or product key is absent from its dimension.

    ── TX-04 / TX-05 · Orphaned dimension keys ───────────────────────────────
    WHY: ``seed_data.py`` lines 179-184 plant five transactions on stores
    S016-S019 and three on products P031-P032, none of which exist in the
    dimension files. Both sets sit immediately past the last real key (S015,
    P030), which is the signature of a stale dimension extract rather than of
    corrupt transactions -- new stores and products opened and the master data
    was never refreshed.
    DECISION: quarantine both classes and exclude them from ``fact_sales``, with
    the withheld revenue stated as a number in the audit report so the exclusion
    is a disclosure rather than a silence.
    ALTERNATIVE REJECTED (1): dropping them silently. Loses real revenue with no
    trace, which is the one outcome nobody can defend.
    ALTERNATIVE REJECTED (2): routing them to an "Unknown Store" / "Unknown
    Product" dimension member. This keeps the money in the totals, which is
    tempting -- but it pollutes every store- and product-level metric with a
    bucket nobody can act on, and it asserts that these sales belong to an entity
    that does not exist. Quarantining keeps the star schema clean, lets
    ``PRAGMA foreign_keys = ON`` be genuinely enforced rather than decorative,
    and leaves the rows on disk, counted and priced, waiting for the missing
    master data.

    The two codes get **deliberately identical treatment**, because they are the
    same structural problem; giving two identical defects two different policies
    is the inconsistency a reviewer would rightly attack first.

    Args:
        df: Working frame.
        audit: Ledger to record into.
        valid_store_ids: Surviving ``store_id`` values from the cleaned store
            dimension. Passed in rather than re-read, so the check is against
            what will *actually* be loaded -- including the ST-02 survivorship
            outcome.
        valid_product_ids: Surviving ``product_id`` values, likewise.

    Returns:
        The frame with ``_wk_is_orphan_store`` and ``_wk_is_orphan_product``.

    Defects handled: TX-04, TX-05.
    """
    work = df.copy()
    stores = {str(s) for s in valid_store_ids}
    products = {str(p) for p in valid_product_ids}

    # DEFECT: TX-04
    orphan_store = ~work["store_id"].isin(stores)
    # DEFECT: TX-05
    orphan_product = ~work["product_id"].isin(products)
    work[f"{_WORK}is_orphan_store"] = orphan_store.astype(bool)
    work[f"{_WORK}is_orphan_product"] = orphan_product.astype(bool)

    for code, mask, key_column, dimension in (
        (DefectCode.TX_04_ORPHAN_STORE, orphan_store, "store_id", "dim_store"),
        (DefectCode.TX_05_ORPHAN_PRODUCT, orphan_product, "product_id", "dim_product"),
    ):
        unknown_keys = sorted(work.loc[mask, key_column].dropna().unique().tolist())
        withheld = round_money(float(work.loc[mask, f"{_WORK}total_amount"].sum()))
        audit.record(
            DefectRecord(
                code=code,
                detected_count=int(mask.sum()),
                action="quarantined",
                affected_keys=work.loc[mask, "transaction_id"].tolist(),
                notes=(
                    f"{int(mask.sum())} transactions reference {len(unknown_keys)} "
                    f"{key_column} value(s) absent from {dimension}: "
                    f"{', '.join(unknown_keys) if unknown_keys else 'none'}. "
                    f"${withheld:,.2f} of revenue is withheld from fact_sales and written to "
                    f"output/quarantine/transactions__{code.value}.csv rather than dropped "
                    "silently or routed to an 'Unknown' dimension member. The keys sit "
                    "immediately past the last catalogued one, which points at a stale "
                    "dimension extract -- an operational fix, not corrupt transactions."
                ),
            )
        )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# TX-03 · Silent discount / reconciliation break
# ══════════════════════════════════════════════════════════════════════════════
def reconcile_totals(
    df: pd.DataFrame,
    audit: AuditLog,
    tolerance: float = PRICE_TOLERANCE,
) -> pd.DataFrame:
    """Expose the gap between the reported total and ``quantity * unit_price``.

    ── TX-03 · Silent discount ───────────────────────────────────────────────
    WHY: ``seed_data.py`` lines 174-176 mark rows 100-119 down by a random 5-20%,
    leaving ``total_amount != quantity * unit_price`` on twenty rows.
    **THIS IS REAL REVENUE, NOT AN ERROR.** The money genuinely moved at the
    discounted price. ``total_amount`` is the fact; ``quantity * unit_price`` is a
    derivation from two other columns. When a fact and a derivation disagree, the
    fact wins and the derivation is what needs explaining.
    DECISION: preserve ``total_amount`` byte-for-byte as reported and *add*
    columns -- ``extended_amount`` (the list value), ``discount_amount`` (the
    delta) and ``has_discount`` (the flag). Nothing is overwritten, no row is
    removed, and the discrepancy becomes a reportable finding instead of a
    rounding-shaped mystery.
    ALTERNATIVE REJECTED: ``total_amount = quantity * unit_price``, which is what
    the previous solution did at ``cleaner.py:116``. **This is the single worst
    mistake available in this challenge**, for two compounding reasons. First, it
    inflates revenue by the entire discount pool (quantified in the audit note
    below, to the cent). Second and far worse, it *erases the evidence*: once the
    totals agree, there is no way to tell that a discount ever happened, so the
    finding can never be reported and the business never learns that an unmodelled
    promotion or manual override is flowing through a schema with nowhere to
    record it. The discrepancy IS the insight.

    Keeping both numbers side by side is also what makes the
    ``revenue_reconciliation`` metric possible: gross list value minus discounts
    minus returns must tie back to net revenue, line by line, so the whole chain
    is checkable rather than asserted.

    Zero-quantity rows (TX-07) are excluded from the discount population: for them
    ``0 == 0 * price`` is trivially true, so including them would pad the "clean"
    side of the ratio with rows that carry no economics at all.

    Args:
        df: Working frame with parsed measures.
        audit: Ledger to record into.
        tolerance: Dollar threshold above which a gap is a real discount.
            Defaults to :data:`src.config.PRICE_TOLERANCE`.

    Returns:
        The frame with ``_wk_extended_amount``, ``_wk_discount_amount`` and
        ``_wk_has_discount`` added.

    Defects handled: TX-03.
    """
    work = df.copy()

    # WHY a row-wise call into rules.reconcile_line_amount rather than a
    # vectorised expression: the rule -- "the reported total is never rewritten"
    # -- lives in one pure, unit-testable function, and this module calls it. A
    # duplicated vectorised formula here is exactly how the two would drift, and
    # the direction they drift in is somebody re-deriving the total.
    reconciled = [
        reconcile_line_amount(
            quantity=0 if pd.isna(q) else q,
            unit_price=0.0 if pd.isna(p) else p,
            total_amount=0.0 if pd.isna(t) else t,
            tolerance=tolerance,
        )
        for q, p, t in zip(
            work[f"{_WORK}quantity"], work[f"{_WORK}unit_price"], work[f"{_WORK}total_amount"]
        )
    ]

    work[f"{_WORK}extended_amount"] = [r.extended_amount for r in reconciled]
    # DEFECT: TX-03
    work[f"{_WORK}discount_amount"] = [r.discount_amount for r in reconciled]

    # WHY the discount population excludes unusable and zero-quantity rows: a row
    # whose measures did not parse has no meaningful gap, and a zero-quantity row
    # reconciles trivially. Neither is evidence about discounting.
    eligible = ~work[f"{_WORK}amount_unparsed"] & ~work[f"{_WORK}is_zero_quantity"]
    has_discount = pd.Series([r.has_discount for r in reconciled], index=work.index) & eligible
    work[f"{_WORK}has_discount"] = has_discount.astype(bool)

    discount_total = round_money(float(work.loc[has_discount, f"{_WORK}discount_amount"].sum()))
    extended_total = round_money(float(work.loc[has_discount, f"{_WORK}extended_amount"].sum()))
    reported_total = round_money(float(work.loc[has_discount, f"{_WORK}total_amount"].sum()))
    pcts = [
        r.discount_pct
        for r, keep in zip(reconciled, has_discount)
        if keep and r.discount_pct is not None
    ]
    pct_range = f"{min(pcts) * 100:.1f}%-{max(pcts) * 100:.1f}%" if pcts else "n/a"
    overall_reported = round_money(float(work[f"{_WORK}total_amount"].sum()))
    overstatement_pct = (discount_total / overall_reported * 100) if overall_reported else 0.0

    audit.record(
        DefectRecord(
            code=DefectCode.TX_03_SILENT_DISCOUNT,
            detected_count=int(has_discount.sum()),
            action="preserved",
            affected_keys=work.loc[has_discount, "transaction_id"].tolist(),
            notes=(
                f"{int(has_discount.sum())} rows have |quantity * unit_price - total_amount| > "
                f"${tolerance:.2f}. Total discount value: ${discount_total:,.2f} "
                f"(list ${extended_total:,.2f} vs reported ${reported_total:,.2f}), individual "
                f"discounts ranging {pct_range}. total_amount is PRESERVED as reported and is "
                "authoritative for revenue; the delta is exposed as discount_amount with "
                f"has_discount=True. Recomputing total_amount = quantity * unit_price would have "
                f"overstated total revenue by ${discount_total:,.2f} "
                f"({overstatement_pct:.2f}% of the reported total) AND erased the finding "
                "entirely, so nobody would ever learn that an unmodelled promotion or manual "
                "override is flowing through this schema. Zero-quantity rows are excluded from "
                "this population because 0 == 0 * price reconciles trivially."
            ),
        )
    )
    return work


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 · Apply the decisions, once, with a stated precedence
# ══════════════════════════════════════════════════════════════════════════════
# WHY a single explicit ladder rather than a chain of `df = df[~mask]` filters:
# a chain hides the precedence in its ordering and makes the row arithmetic
# unverifiable, because each stage only knows about the survivors of the last.
# Here every disposition is computed against the same 505-row frame, so the
# reconciliation is a sum over one population and a reviewer can add it up.
#
# PRECEDENCE, and why this order:
#   1. TX-09 duplicate   -- a duplicate copy is not an independent row at all, so
#                           it is resolved first; anything else about it is a
#                           property of the original, which survives.
#   2. TX-01 unparsed    -- structural: without a date the row has no grain.
#   3. TX-02 unparsed    -- structural: without measures the row has no value.
#   4. TX-07 zero qty    -- economically empty: the row exists but means nothing.
#   5. TX-08 future date -- temporally invalid: real money, wrong period.
#   6. TX-04 orphan store, 7. TX-05 orphan product -- referential: the row is
#                           fine, the dimension is missing. Last because it is the
#                           most recoverable of the exclusions.
# Structural before semantic before referential. The precedence only decides
# which single reason_code a row is FILED under in the lineage; each defect's
# detected_count and each quarantine CSV use that defect's FULL source mask, so
# no evidence is lost to the ordering. The overlap between masks is counted and
# published rather than assumed to be zero (it is zero in this file).
_QUARANTINE_PRECEDENCE: Final[tuple[tuple[DefectCode, str, str], ...]] = (
    (DefectCode.TX_01_MIXED_DATE_FORMATS, f"{_WORK}date_unparsed",
     "transaction_date matched no format in config.DATE_FORMATS"),
    (DefectCode.TX_02_STRING_CURRENCY, f"{_WORK}amount_unparsed",
     "quantity, unit_price or total_amount could not be parsed as a number"),
    (DefectCode.TX_07_ZERO_QUANTITY, f"{_WORK}is_zero_quantity",
     "quantity == 0: economically empty row, excluded to protect the AOV denominator"),
    (DefectCode.TX_08_FUTURE_DATE, f"{_WORK}is_future_dated",
     "transaction_date is after AS_OF_DATE"),
    (DefectCode.TX_04_ORPHAN_STORE, f"{_WORK}is_orphan_store",
     "store_id is absent from the cleaned store dimension"),
    (DefectCode.TX_05_ORPHAN_PRODUCT, f"{_WORK}is_orphan_product",
     "product_id is absent from the cleaned product dimension"),
)

_QUARANTINE_VIEW_COLUMNS: Final[tuple[str, ...]] = (
    "source_row",
    "transaction_id",
    "transaction_date",
    "store_id",
    "product_id",
    "customer_id",
    "quantity",
    "unit_price",
    "total_amount",
    "parsed_transaction_date",
    "parsed_quantity",
    "parsed_unit_price",
    "parsed_total_amount",
    "reason_code",
    "reason_detail",
)


def _quarantine_view(work: pd.DataFrame, mask: pd.Series, code: DefectCode, detail: str) -> pd.DataFrame:
    """Build the human-readable evidence frame written to ``output/quarantine/``.

    WHY the raw source columns AND the parsed ones: a reviewer opening a
    quarantine file needs to see what the source said (to judge whether the
    rejection was fair) and what the pipeline made of it (to judge whether the
    parser was at fault). Either alone leaves the question half-answered.

    Args:
        work: The full working frame.
        mask: Rows to include.
        code: The defect this slice is evidence for.
        detail: Human-readable reason, echoed into every row.

    Returns:
        A frame with :data:`_QUARANTINE_VIEW_COLUMNS`.

    Defects handled: TX-01, TX-02, TX-04, TX-05, TX-07, TX-08, TX-09 (evidence).
    """
    slice_ = work.loc[mask].copy()
    view = pd.DataFrame(
        {
            "source_row": slice_[f"{_WORK}source_row"],
            "transaction_id": slice_["transaction_id"],
            "transaction_date": slice_["transaction_date"],
            "store_id": slice_["store_id"],
            "product_id": slice_["product_id"],
            "customer_id": slice_["customer_id"],
            "quantity": slice_["quantity"],
            "unit_price": slice_["unit_price"],
            "total_amount": slice_["total_amount"],
            "parsed_transaction_date": slice_[f"{_WORK}transaction_date"],
            "parsed_quantity": slice_[f"{_WORK}quantity"],
            "parsed_unit_price": slice_[f"{_WORK}unit_price"],
            "parsed_total_amount": slice_[f"{_WORK}total_amount"],
            "reason_code": code.value,
            "reason_detail": detail,
        }
    )
    return view.loc[:, list(_QUARANTINE_VIEW_COLUMNS)]


def _apply_decisions(
    work: pd.DataFrame,
    audit: AuditLog,
    lineage_dir: Path | None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Resolve every row to kept / dropped / quarantined, and prove the arithmetic.

    Args:
        work: The fully-detected working frame (all source rows still present).
        audit: Ledger; receives one quarantine slice per excluded defect code.
        lineage_dir: Directory for ``transactions__lineage.csv``. ``None`` skips
            the write (used by unit tests that must stay filesystem-free).

    Returns:
        ``(kept_frame, lineage_frame, reconciliation_dict)``.

    Raises:
        AssertionError: If kept + dropped + quarantined != the source row count.
            WHY raise rather than log: an unbalanced ledger means at least one row
            was silently created or destroyed, which invalidates every number the
            pipeline is about to publish. There is no useful way to continue.

    Defects handled: TX-01, TX-02, TX-04, TX-05, TX-07, TX-08, TX-09 (disposition).
    """
    source_rows = len(work)
    duplicate_mask = work[f"{_WORK}is_exact_duplicate"].astype(bool)

    # ── Assign exactly one reason per row, by the stated precedence ───────────
    reason_code = pd.Series([None] * source_rows, index=work.index, dtype=object)
    reason_detail = pd.Series([None] * source_rows, index=work.index, dtype=object)

    reason_code[duplicate_mask] = DefectCode.TX_09_EXACT_DUPLICATE.value
    reason_detail[duplicate_mask] = (
        "byte-identical repeat of an earlier source row (de-duplicated on the full raw row)"
    )

    quarantine_union = pd.Series(False, index=work.index)
    overlap_rows = 0
    for code, column, detail in _QUARANTINE_PRECEDENCE:
        mask = work[column].astype(bool)
        if not mask.any():
            # WHY skip empty slices instead of writing a 0-row CSV: an empty
            # quarantine file reads as "we found some" at a glance and is a
            # gratuitous source of confusion in the output directory.
            continue

        # Full mask -> the quarantine CSV, so each defect's evidence is complete
        # even where a row also qualifies under a higher-precedence code.
        audit.quarantine("transactions", _quarantine_view(work, mask, code, detail), code)

        unclaimed = mask & reason_code.isna()
        overlap_rows += int((mask & ~unclaimed & ~duplicate_mask).sum())
        reason_code[unclaimed] = code.value
        reason_detail[unclaimed] = detail
        quarantine_union |= mask & ~duplicate_mask

    # The dropped duplicates are quarantined too -- not because they are salvageable
    # but because "nothing is ever deleted without a trace" has to include the rows
    # we are most confident about, or the 505-row proof cannot be reconstructed
    # from disk alone.
    if duplicate_mask.any():
        audit.quarantine(
            "transactions",
            _quarantine_view(
                work,
                duplicate_mask,
                DefectCode.TX_09_EXACT_DUPLICATE,
                "byte-identical repeat of an earlier source row",
            ),
            DefectCode.TX_09_EXACT_DUPLICATE,
        )

    kept_mask = ~duplicate_mask & ~quarantine_union
    reason_code[kept_mask] = None
    reason_detail[kept_mask] = None

    # ── Row-level lineage for all source rows ────────────────────────────────
    disposition = pd.Series("kept", index=work.index, dtype=object)
    disposition[quarantine_union] = "quarantined"
    disposition[duplicate_mask] = "dropped"

    lineage = pd.DataFrame(
        {
            "source_row": work[f"{_WORK}source_row"],
            "transaction_id": work["transaction_id"],
            "disposition": disposition,
            "reason_code": reason_code,
            "reason_detail": reason_detail,
            "total_amount_as_reported": work[f"{_WORK}total_amount"],
        }
    ).sort_values("source_row", kind="stable")

    kept_count = int(kept_mask.sum())
    dropped_count = int(duplicate_mask.sum())
    quarantined_count = int(quarantine_union.sum())

    # ── The completeness proof ───────────────────────────────────────────────
    # Every source row is kept, dropped or quarantined -- exactly one of the
    # three. A reviewer adds these three numbers and gets the source row count.
    assert kept_count + dropped_count + quarantined_count == source_rows, (
        f"Row reconciliation failed: kept {kept_count} + dropped {dropped_count} + "
        f"quarantined {quarantined_count} = "
        f"{kept_count + dropped_count + quarantined_count}, expected {source_rows}. "
        "At least one row was created or destroyed without a disposition."
    )

    reconciliation: dict[str, Any] = {
        "source_rows": source_rows,
        "kept": kept_count,
        "dropped": dropped_count,
        "quarantined": quarantined_count,
        "balances": kept_count + dropped_count + quarantined_count == source_rows,
        "dropped_by_code": {DefectCode.TX_09_EXACT_DUPLICATE.value: dropped_count},
        "quarantined_by_reason_code": {
            str(k): int(v)
            for k, v in lineage.loc[
                lineage["disposition"].eq("quarantined"), "reason_code"
            ]
            .value_counts()
            .items()
        },
        "rows_matching_more_than_one_quarantine_code": overlap_rows,
        "revenue_as_reported_total": round_money(float(work[f"{_WORK}total_amount"].sum())),
        "revenue_kept": round_money(float(work.loc[kept_mask, f"{_WORK}total_amount"].sum())),
        "revenue_dropped_duplicates": round_money(
            float(work.loc[duplicate_mask, f"{_WORK}total_amount"].sum())
        ),
        "revenue_quarantined": round_money(
            float(work.loc[quarantine_union, f"{_WORK}total_amount"].sum())
        ),
    }

    if lineage_dir is not None:
        target = Path(lineage_dir) / LINEAGE_FILENAME
        write_dataframe_csv(lineage, target)
        reconciliation["lineage_file"] = str(target)

    return work.loc[kept_mask].copy(), lineage, reconciliation


def _project_output(kept: pd.DataFrame) -> pd.DataFrame:
    """Build the contract-mandated output frame with explicit dtypes.

    WHY an explicit projection rather than renaming in place: the warehouse
    loader selects these thirteen columns by name, so the boundary is an
    interface. Constructing it deliberately means a stray ``_wk_`` scratch column
    can never leak into ``fact_sales``, and the dtypes are asserted here rather
    than discovered by SQLite at load time.

    Args:
        kept: Surviving working rows.

    Returns:
        A frame with exactly :data:`OUTPUT_COLUMNS`, in that order.

    Defects handled: TX-03, TX-06, TX-10 (their flags become columns here).
    """
    out = pd.DataFrame(
        {
            "transaction_id": kept["transaction_id"].astype(str),
            "transaction_date": pd.to_datetime(kept[f"{_WORK}transaction_date"]),
            "store_id": kept["store_id"].astype(str),
            "product_id": kept["product_id"].astype(str),
            "customer_id": kept[f"{_WORK}customer_id"].astype(str),
            "is_guest": kept[f"{_WORK}is_guest"].astype(bool),
            # WHY Int64 -> int64 only here: nullability was needed while failed
            # parses were still in the frame. They have been quarantined, so the
            # column is now provably complete and can take a non-nullable dtype
            # that SQLite maps cleanly to INTEGER.
            "quantity": kept[f"{_WORK}quantity"].astype("int64"),
            "unit_price": kept[f"{_WORK}unit_price"].astype("float64"),
            "extended_amount": kept[f"{_WORK}extended_amount"].astype("float64"),
            "total_amount": kept[f"{_WORK}total_amount"].astype("float64"),
            "discount_amount": kept[f"{_WORK}discount_amount"].astype("float64"),
            "has_discount": kept[f"{_WORK}has_discount"].astype(bool),
            "is_return": kept[f"{_WORK}is_return"].astype(bool),
        }
    ).reset_index(drop=True)
    return out.loc[:, list(OUTPUT_COLUMNS)]


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════
def clean_transactions(
    df: pd.DataFrame,
    audit: AuditLog,
    valid_store_ids: set[str],
    valid_product_ids: set[str],
    *,
    as_of_date: dt.date = AS_OF_DATE,
    tolerance: float = PRICE_TOLERANCE,
    lineage_dir: Path | None = QUARANTINE_DIR,
) -> pd.DataFrame:
    """Clean ``transactions.csv``: detect ten defect classes, decide, and account.

    Runs the two-phase sequence documented in the module header -- detect every
    defect against the full source population, then apply the decisions once --
    and guarantees that every input row leaves as exactly one of kept, dropped or
    quarantined.

    Args:
        df: All-string frame from :func:`src.io_utils.read_csv_as_str`
            (505 rows for the shipped dataset).
        audit: Shared ledger. Mutated in place; ten :class:`DefectRecord` entries
            and up to seven quarantine slices are added (contract §4: the audit is
            mutated, never returned separately).
        valid_store_ids: ``store_id`` values surviving in the cleaned store
            dimension, used for TX-04.
        valid_product_ids: ``product_id`` values surviving in the cleaned product
            dimension, used for TX-05.
        as_of_date: Reference "today" for TX-08. Defaults to
            :data:`src.config.AS_OF_DATE` (2026-06-02). Never ``datetime.now()``.
        tolerance: Dollar threshold for the TX-03 reconciliation break. Defaults
            to :data:`src.config.PRICE_TOLERANCE`.
        lineage_dir: Where to write ``transactions__lineage.csv``. Pass ``None``
            to suppress the write.

    Returns:
        A DataFrame with exactly :data:`OUTPUT_COLUMNS`. Two extras ride along in
        ``.attrs`` for callers that want them and are ignored by everyone else:

        * ``attrs["_cleaning_lineage"]`` -- one row per **source** row with its
          disposition and reason code (the same data as the lineage CSV).
        * ``attrs["_cleaning_reconciliation"]`` -- the kept/dropped/quarantined
          arithmetic and the revenue attached to each bucket.

    Raises:
        ValueError: If a contract source column is missing.
        AssertionError: If the row arithmetic does not balance, or if any row was
            lost to date parsing (TX-01's guarantee).

    Defects handled: TX-01, TX-02, TX-03, TX-04, TX-05, TX-06, TX-07, TX-08,
        TX-09, TX-10 -- all ten transaction defect classes.
    """
    _validate_source_shape(df)
    source_rows = len(df)

    # ── Phase 1 · DETECT against the full source population ──────────────────
    # Every count recorded below is taken from a mask over all `source_rows`
    # rows. Nothing is filtered until Phase 2. See the module header for why.
    work = _normalise_source(df)
    work = drop_exact_duplicates(work, audit)          # TX-09 (detect only)
    work = parse_transaction_dates(work, audit)        # TX-01 -> dates are typed
    work = parse_amounts(work, audit)                  # TX-02 -> measures are typed
    work = handle_guest_customers(work, audit)         # TX-06 (needs typed totals)
    work = flag_returns(work, audit)                   # TX-10 (needs typed measures)
    work = flag_zero_quantity(work, audit)             # TX-07 (before TX-03: see below)
    work = reconcile_totals(work, audit, tolerance)    # TX-03 (needs TX-02 and TX-07)
    work = check_referential_integrity(                # TX-04, TX-05
        work, audit, valid_store_ids, valid_product_ids
    )
    work = flag_future_dates(work, audit, as_of_date)  # TX-08 (strictly after TX-01)

    # ── TX-01's hard guarantee ───────────────────────────────────────────────
    # WHY assert rather than trust: "no row is lost to date parsing" is the
    # central claim of the TX-01 fix, and the previous solution failed exactly
    # here while reporting success. An assertion turns the claim into a test that
    # runs on every execution against real data.
    unparsed_dates = int(work[f"{_WORK}date_unparsed"].sum())
    assert unparsed_dates == 0, (
        f"{unparsed_dates} transaction_date value(s) matched no format in config.DATE_FORMATS. "
        "TX-01 guarantees zero rows lost to date parsing; add the missing format rather than "
        "coercing these rows to NaT."
    )

    # ── Phase 2 · DECIDE: one disposition per row, arithmetic proven ─────────
    kept, lineage, reconciliation = _apply_decisions(work, audit, lineage_dir)
    cleaned = _project_output(kept)

    assert len(cleaned) == reconciliation["kept"], (  # pragma: no cover - guard
        "Projection changed the row count; the output no longer matches the lineage."
    )

    # WHY attrs and not extra columns or a second return value: the signature is
    # fixed by contract §4 and the column list by the warehouse loader, so neither
    # can carry this. attrs is pandas' documented side-channel for exactly this,
    # and a consumer that ignores it is unaffected.
    cleaned.attrs["_cleaning_lineage"] = lineage
    cleaned.attrs["_cleaning_reconciliation"] = reconciliation
    cleaned.attrs["_source_row_count"] = source_rows
    return cleaned


__all__ = [
    "LINEAGE_FILENAME",
    "OUTPUT_COLUMNS",
    "SOURCE_COLUMNS",
    "check_referential_integrity",
    "clean_transactions",
    "drop_exact_duplicates",
    "flag_future_dates",
    "flag_returns",
    "flag_zero_quantity",
    "handle_guest_customers",
    "parse_amounts",
    "parse_transaction_dates",
    "reconcile_totals",
]
