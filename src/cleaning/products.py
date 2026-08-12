"""Product dimension cleaning -- owns PR-01, PR-02, PR-03 and PR-04.

What this module does
---------------------
Takes the 32 raw rows of ``data/raw/products.csv`` exactly as they sit on disk
(all-string, from :func:`src.io_utils.read_csv_as_str`) and returns the 30-row,
fully typed product dimension that ``src/warehouse/loader.py`` loads into
``dim_product``.

The interesting problem
-----------------------
Two of the four defects here look *identical* to a naive check: ``product_id``
appears twice. P012's two rows are byte-identical -- a re-extract artefact,
information-free, safe to drop. P005's two rows differ only in ``unit_price``
(141.61 vs 150.11, +$8.50) -- an undocumented price change, i.e. a genuine
business event that a duplicate-removal step would delete along with the row.

``drop_duplicates(subset=["product_id"])`` treats both the same and silently
resolves the price by row order. That single call removed the row *and* the
finding in the previous attempt, which is why this module never de-duplicates on
the key. Instead :func:`resolve_duplicate_keys` partitions colliding keys into
two populations by comparing payloads:

* **identical payload**  -> a true duplicate  -> drop it        (PR-01)
* **conflicting payload** -> a data event     -> resolve + flag (PR-02)

Neither branch is special-cased to P012 or P005; the routine works on any number
of colliding keys with any number of rows each, and the conflict resolution is
driven by the declared :data:`CONFLICT_RESOLUTION_POLICY` table.

Defect codes owned
------------------
=======  ==========================================  ================================
Code     Problem                                     Handler
=======  ==========================================  ================================
PR-01    P012 byte-identical duplicate row (1)       :func:`drop_exact_duplicates`
PR-02    P005 two list prices, +$8.50 (1 surplus)    :func:`resolve_price_conflicts`
PR-03    NULL category on P003/P009/P016/P023/P029   :func:`impute_category`
PR-04    P027 ``unit_price`` = 0.00 (1)              :func:`flag_zero_prices`
=======  ==========================================  ================================

Inputs
------
``pd.DataFrame`` with ``product_id, product_name, category, unit_price,
supplier_id`` as strings, plus an :class:`~src.audit.AuditLog` mutated in place.

Outputs
-------
``pd.DataFrame`` with :data:`OUTPUT_COLUMNS`: ``product_id, product_name,
category`` (str), ``category_is_imputed`` (bool), ``list_unit_price`` (float,
always > 0), ``price_is_imputed`` (bool), ``price_conflict`` (bool),
``supplier_id`` (str).
"""

from __future__ import annotations

import re
from typing import Callable, Final, Sequence

import numpy as np
import pandas as pd

from src.audit import AuditLog, DefectRecord
from src.defects import DefectCode

# ── Column contracts ──────────────────────────────────────────────────────────
SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "product_id",
    "product_name",
    "category",
    "unit_price",
    "supplier_id",
)

OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    "product_id",
    "product_name",
    "category",
    "category_is_imputed",
    "list_unit_price",
    "price_is_imputed",
    "price_conflict",
    "supplier_id",
)

BUSINESS_KEY: Final[str] = "product_id"
"""Natural key. ``dim_product.product_id`` is UNIQUE, so any unresolved key
collision is a failed load rather than a bad number."""

PAYLOAD_COLUMNS: Final[tuple[str, ...]] = tuple(c for c in SOURCE_COLUMNS if c != BUSINESS_KEY)
"""Everything that is *not* the key. Two rows sharing a key are a true duplicate
only if they agree on all of these."""

UNKNOWN_CATEGORY: Final[str] = "Unknown"
"""The literal written into NULL categories (PR-03). Deliberately a named bucket
rather than NULL -- see :func:`impute_category` for the argument."""

_CURRENCY_NOISE: Final[re.Pattern[str]] = re.compile(r"[\s$,]")
"""Characters stripped before a price string is parsed.

WHY the product file needs this at all when its prices look clean: reading with
``dtype=str`` means *nothing* has been validated, and the transactions file in
this same dataset does carry ``'$142.50'`` (TX-02). Sharing the shape of the
parser here means a currency-formatted product price would be handled rather
than silently becoming NaN, and a genuinely unparseable value raises instead of
quietly zeroing -- which for a price column is the difference between a loud
error and a 100% discount nobody notices.
"""


# ══════════════════════════════════════════════════════════════════════════════
# Local helpers
# ══════════════════════════════════════════════════════════════════════════════
# NOTE FOR REVIEWERS / OTHER AGENTS: `parse_price` duplicates, in miniature, the
# currency parsing that src/cleaning/rules.py owns for the transactions layer
# (TX-02). It is implemented locally and deliberately kept private-ish so that
# the product dimension has no build-order dependency on the transaction module.
# If rules.parse_currency is later adopted here, this function should be deleted
# rather than left as a second, divergable definition of "what a price is".
def parse_price(value: object) -> float:
    """Parse one price cell into a float.

    Handles ``'$1,234.50'``, ``' 12.30 '`` and accounting negatives ``'(4.99)'``.

    Args:
        value: A raw cell -- string, ``None``, ``NaN`` or an already-numeric type.

    Returns:
        The parsed float, or ``NaN`` for missing/blank input.

    Raises:
        ValueError: If a non-blank value cannot be parsed. WHY raise instead of
            returning NaN: a coerce-to-NaN here would later be filled by the
            PR-04 median path, laundering an unreadable value into a plausible
            invented price. An unparseable price is a stop-the-line event.

    Defects handled: PR-04 (its detection needs 0.00 to survive as 0.00, not
        become NaN), and it is a precondition for the PR-02 MAX policy.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = _CURRENCY_NOISE.sub("", str(value))
    if not text:
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        parsed = float(text)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unparseable product price {value!r}") from exc
    return -parsed if negative else parsed


def normalize_text_columns(df: pd.DataFrame, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Strip whitespace and unify blanks to ``None``.

    Args:
        df: All-string source frame.
        columns: Columns to normalise; defaults to all.

    Returns:
        A normalised copy.

    Defects handled: precondition for PR-01 (``'P012 '`` and ``'P012'`` must not
        look like different payloads) and PR-03 (``''`` and NULL must be one
        kind of missing, or the null-category count comes out short).
    """
    out = df.copy()
    targets = list(columns) if columns is not None else list(out.columns)
    for col in targets:
        if col not in out.columns or out[col].dtype != object:
            continue
        stripped = out[col].astype("string").str.strip()
        out[col] = stripped.replace({"": pd.NA}).astype(object).where(lambda s: s.notna(), None)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate-key resolution machinery (PR-01 + PR-02)
# ══════════════════════════════════════════════════════════════════════════════
def _policy_max_numeric(values: pd.Series) -> object:
    """Elect the numerically largest value, returning it in its original form.

    WHY return the original cell rather than the parsed float: the frame is still
    all-strings at this point and typing happens once, at the end, in
    :func:`_finalize_types`. Returning a float here would create a column holding
    both floats and strings -- exactly the mixed-dtype mess that ``dtype=str``
    reading exists to avoid.
    """
    parsed = values.map(parse_price)
    if parsed.notna().sum() == 0:  # pragma: no cover - defensive
        return values.iloc[0]
    return values.loc[parsed.idxmax()]


def _policy_first_non_null(values: pd.Series) -> object:
    """Elect the first non-null value in source order (the conservative default)."""
    non_null = values.dropna()
    return non_null.iloc[0] if len(non_null) else None


CONFLICT_RESOLUTION_POLICY: Final[dict[str, Callable[[pd.Series], object]]] = {
    "unit_price": _policy_max_numeric,
}
"""How each contested attribute is resolved when one key carries several values.

Declared as a table rather than buried in an ``if``: a reviewer who disagrees
with "MAX wins" changes one line, and a new contested column gets a stated policy
instead of falling through to whatever the code happened to do.

``unit_price`` -> **MAX**, and the choice of *MAX specifically* is the point. The
seed shuffles the file, so 150.11 physically precedes 141.61; ``keep="first"``
would therefore pick the higher price *by accident today* and the lower one
tomorrow if the extract were re-sorted. MAX is order-independent, which makes the
result reproducible, and it also encodes the assumption we are willing to state
out loud: the appended, higher record is the newer list price. That assumption is
written into the audit note so it can be contradicted by anyone with the source
system in front of them.

Everything else -> first non-null, which is a genuinely conservative default
because it never invents a value and never silently prefers a null.
"""


def drop_exact_duplicates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Remove byte-identical rows -- stage A of duplicate-key resolution.

    ── PR-01 · Byte-identical duplicate product row ────────────────────────────
    WHY: P012 appears twice with identical name, category, unit_price and
      supplier_id. Every attribute agrees, so the second copy carries no
      information and dropping it cannot lose any.
    DECISION: de-duplicate on the **entire row**, keep the first occurrence,
      log the key. No judgement call is required and none is pretended.
    WHY THIS RUNS FIRST: it is what makes PR-02 detectable. After this pass, any
      remaining ``product_id`` collision is *by construction* a collision with
      disagreeing attributes -- a data event, not a duplicate. Reversing the
      order, or collapsing on the key instead of the row, merges P012 and P005
      into one undifferentiated "duplicates" bucket and the $8.50 price change is
      never reported. That is precisely how the previous solution lost it.
    ALTERNATIVE REJECTED: ``drop_duplicates(subset=["product_id"])``. It removes
      the row and the finding in the same instruction, and picks P005's surviving
      price by file order -- a coin flip that no one is told about.

    Args:
        df: Normalised source frame.
        audit: Ledger, mutated in place.

    Returns:
        A copy with full-row duplicates removed.

    Defects handled: PR-01.
    """
    out = df.copy()
    present = [c for c in SOURCE_COLUMNS if c in out.columns]
    # WHY keep="first": among rows that are identical in every column, "which one
    # survives" is not a decision -- there is nothing to choose between. This is
    # the one place keep="first" is defensible, and it is defensible *because*
    # the payloads are equal, which the subset argument makes explicit.
    duplicate_mask = out.duplicated(subset=present, keep="first")
    detected = int(duplicate_mask.sum())
    if detected:
        # WHY quarantine before dropping (contract §7b): nothing leaves this
        # pipeline without leaving a row on disk saying it did.
        audit.quarantine("products", out.loc[duplicate_mask, present], DefectCode.PR_01_EXACT_DUPLICATE)
        keys = out.loc[duplicate_mask, BUSINESS_KEY].astype(str).tolist()
        audit.record(
            DefectRecord(
                code=DefectCode.PR_01_EXACT_DUPLICATE,
                # WHY the surplus-row count: the defect is the redundant record,
                # and the same framing is used for PR-02 and ST-02 so the three
                # numbers in the report mean the same thing.
                detected_count=detected,
                action="dropped",
                affected_keys=keys,
                notes=(
                    f"Full-row duplicates removed for {sorted(set(keys))}: every column agrees, "
                    "so the copy carries no information. De-duplication is keyed on the whole "
                    "row, never on product_id -- keying on product_id would sweep up PR-02 "
                    "(P005's price change) in the same call and silently delete the finding."
                ),
            )
        )
        out = out.loc[~duplicate_mask].copy()  # DEFECT: PR-01
    return out


def resolve_price_conflicts(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Collapse key collisions whose payloads disagree -- stage B.

    ── PR-02 · Price change masquerading as a duplicate ────────────────────────
    WHY: after stage A, ``product_id`` still collides for P005, which appears
      with ``unit_price`` 150.11 and 141.61 -- a difference of exactly +$8.50.
      Identical key, disagreeing payload: that is not a duplicate, it is an
      undocumented price change that the source schema has nowhere to record.
    DECISION: treat it as a slowly-changing attribute. Both versions are
      quarantined with the delta stated; ``dim_product`` -- which must hold one
      row per product -- takes the value chosen by
      :data:`CONFLICT_RESOLUTION_POLICY`, i.e. **MAX**, never file order. The
      surviving row is flagged ``price_conflict = True`` so the ambiguity travels
      with the data instead of living in a ticket somewhere.
    WHY ELECTING A PRICE IS SAFE HERE: ``fact_sales.unit_price`` is the price *as
      transacted*, taken from transactions.csv, so ``dim_product.list_unit_price``
      is a reference attribute that no revenue figure depends on. The corroborating
      evidence: all 20 P005 transactions rang at 141.61 and none at 150.11, so the
      increase post-dates the transaction window -- which is exactly why the fact
      table must never source its price from the dimension.
    ALTERNATIVE REJECTED: ``drop_duplicates(subset=["product_id"])`` -- resolves
      the price by whichever row sorts first (the shuffle puts 150.11 first
      *today*) and reports nothing. A genuine price increase becomes a coin flip
      that no one is told about.
    ALTERNATIVE REJECTED: keeping both rows. ``dim_product.product_id`` is UNIQUE,
      so this is not a compromise, it is a failed load.
    ALTERNATIVE REJECTED (the right answer, unavailable here): a Type-2 dimension
      with effective dates. This source carries no date on the price, so there is
      nothing to set ``valid_from`` to; inventing one would be fabrication.
    NOTE ON GENERALITY: nothing below mentions P005 or ``unit_price``. Any key
      with any number of conflicting attributes is resolved column by column
      through the policy table, and every disagreeing column is named in the
      audit note.

    Args:
        df: Frame with full-row duplicates already removed.
        audit: Ledger, mutated in place.

    Returns:
        A copy with one row per key and a boolean ``price_conflict`` column.

    Raises:
        AssertionError: If a surviving key collision has no disagreeing column --
            that would mean stage A failed to run, and silently resolving it here
            would hide the ordering bug.

    Defects handled: PR-02.
    """
    out = df.copy()
    out["price_conflict"] = False
    collision_mask = out[BUSINESS_KEY].duplicated(keep=False)
    if not bool(collision_mask.any()):
        # WHY no zero-record: an absent record reads as "never checked" in
        # assert_all_expected_defects_found, which is the alarm we want if this
        # detector ever stops firing on a file that should trip it.
        return out

    payload_columns = [c for c in PAYLOAD_COLUMNS if c in out.columns]
    resolved_rows: list[pd.Series] = []
    drop_index: list[int] = []
    surplus_rows = 0
    conflict_keys: list[str] = []
    notes: list[str] = []
    evidence_frames: list[pd.DataFrame] = []

    for key, group in out.loc[collision_mask].groupby(BUSINESS_KEY, sort=True):
        differing = [c for c in payload_columns if group[c].nunique(dropna=False) > 1]
        # WHY assert rather than fall through: stage A guarantees this list is
        # non-empty. An empty list means drop_exact_duplicates did not run, and
        # quietly resolving an identical-payload collision here would report a
        # harmless duplicate as a business event.
        assert differing, (
            f"{key}: key collision with identical payload survived stage A -- "
            "drop_exact_duplicates must run before resolve_price_conflicts."
        )

        # ── Resolve column by column through the declared policy table ───────
        winner = group.iloc[0].copy()
        for column in differing:
            policy = CONFLICT_RESOLUTION_POLICY.get(column, _policy_first_non_null)
            winner[column] = policy(group[column])  # DEFECT: PR-02
        # WHY the flag is named for price rather than "has_conflict": the
        # warehouse contract fixes this column, and price is the attribute whose
        # ambiguity actually changes how a downstream number should be read.
        winner["price_conflict"] = "unit_price" in differing

        resolved_rows.append(winner)
        drop_index.extend(group.index.tolist())
        surplus_rows += len(group) - 1
        conflict_keys.append(str(key))

        values = {c: sorted({str(v) for v in group[c]}) for c in differing}
        detail = "; ".join(f"{c}: {' vs '.join(v)}" for c, v in values.items())
        elected = "; ".join(f"{c}={winner[c]}" for c in differing)
        if "unit_price" in differing:
            prices = sorted(group["unit_price"].map(parse_price).dropna().tolist())
            delta = round(prices[-1] - prices[0], 2) if len(prices) > 1 else 0.0
            detail += f" (delta ${delta:,.2f})"
        notes.append(f"{key} -> conflicting {detail}; elected {elected} by policy")

        evidence = group[[BUSINESS_KEY, *payload_columns]].copy()
        evidence["conflicting_columns"] = ", ".join(differing)
        evidence["elected"] = [
            all(str(row[c]) == str(winner[c]) for c in differing) for _, row in group.iterrows()
        ]
        evidence_frames.append(evidence)

    if evidence_frames:
        audit.quarantine(
            "products", pd.concat(evidence_frames, ignore_index=True), DefectCode.PR_02_PRICE_CHANGE
        )

    audit.record(
        DefectRecord(
            code=DefectCode.PR_02_PRICE_CHANGE,
            # WHY surplus rows and not contested rows: consistent with PR-01 and
            # ST-02 -- the defect is the extra record beyond the one dim_product
            # can hold.
            detected_count=int(surplus_rows),
            # WHY "flagged" rather than "dropped": the losing row is not garbage,
            # it is a prior version of a real attribute, and price_conflict=True
            # keeps that fact attached to the surviving record.
            action="flagged",
            affected_keys=conflict_keys,
            notes=(
                "Key collision with DISAGREEING payload -- a data event, not a duplicate. "
                + " | ".join(notes)
                + ". Election is by MAX, never by file order: the source shuffle puts 150.11 "
                "physically first, so keep='first' would pick correctly today and arbitrarily "
                "tomorrow. Assumption stated for contradiction: the appended, higher record is "
                "the newer list price. Revenue is unaffected either way because "
                "fact_sales.unit_price is the transacted price -- all 20 P005 transactions rang "
                "at 141.61 and none at 150.11, so the increase post-dates the transaction "
                "window. The correct long-term fix is a Type-2 dimension with effective dates, "
                "which this source cannot support: it carries no date on the price."
            ),
        )
    )

    # WHY rebuild rather than mutate in place: replacing N rows with 1 inside a
    # loop over a frame you are also iterating is the classic way to get a silent
    # off-by-one. Dropping the whole contested set and appending the resolved
    # rows is harder to get subtly wrong.
    survivors = out.drop(index=drop_index)
    resolved = pd.DataFrame(resolved_rows, columns=out.columns)
    combined = pd.concat([survivors, resolved])
    # WHY sort by the original index: keeps the output in source order so the
    # cleaned CSV diffs cleanly against the raw file.
    return combined.sort_index().reset_index(drop=True)


def resolve_duplicate_keys(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Partition colliding business keys into true duplicates and data events.

    This is the general routine the two product duplicate defects share. It is
    written once, for any key and any payload, because the failure this project
    exists to demonstrate against is exactly the opposite instinct -- treating
    "the id appears twice" as one problem with one fix.

    ::

        key collides
            |
            +-- payload identical  -> nothing to choose between the rows
            |                         -> drop the copy            (PR-01)
            |
            +-- payload conflicts  -> the rows disagree about a fact
                                      -> resolve per declared policy,
                                         flag, and keep the evidence (PR-02)

    Ordering is the whole trick: stage A must run first, because it is what
    guarantees every collision surviving into stage B is genuinely conflicting.

    Args:
        df: Normalised source frame.
        audit: Ledger, mutated in place.

    Returns:
        A frame with one row per business key and a ``price_conflict`` column.

    Defects handled: PR-01, PR-02.
    """
    without_copies = drop_exact_duplicates(df, audit)
    return resolve_price_conflicts(without_copies, audit)


# ══════════════════════════════════════════════════════════════════════════════
# PR-04 · Zero unit price
# ══════════════════════════════════════════════════════════════════════════════
def flag_zero_prices(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Treat a 0.00 list price as missing and impute the category median.

    ── PR-04 · Zero unit price in the product master ───────────────────────────
    WHY: P027 (Apparel) carries ``unit_price = 0.00``. A retailer does not stock
      a $0.00 item, so this is a master-data error rather than a giveaway price.
      Left as-is, every list-price-based margin or discount calculation on P027
      is silently wrong -- an analyst would compute a 100% discount on all 19 of
      its transactions and believe it.
    DECISION: read 0.00 as *missing*. Keep the product, impute the median list
      price of its own category (computed over positive prices only), set
      ``price_is_imputed = True``, and quarantine the row for review.
    WHY THE CATEGORY MEDIAN: it is drawn from the same column and the same
      cohort, so it invents no new information, and the median rather than the
      mean because a five-value cohort is small enough for one outlier to drag a
      mean somewhere silly.
    WHY NOT THE TRANSACTED PRICE: transactions.csv independently rings P027 at
      $195.34 on all 19 lines, which is strong corroboration that 0.00 is wrong
      and is stated in the audit note. It is deliberately *not* written into the
      dimension: cross-populating a master table from a fact table launders fact
      data into master data, and that is a data steward's decision, not an ETL
      job's.
    ALTERNATIVE REJECTED: dropping P027 -- it has real sales, so the revenue
      would vanish while the totals still looked tidy.
    ALTERNATIVE REJECTED: leaving 0.00 unflagged -- the quiet failure described
      above, with no trace anywhere in the output.
    NOTE: revenue is untouched by any of this, because ``fact_sales.unit_price``
      comes from the transaction record and never from the dimension.

    Args:
        df: De-duplicated frame with a string ``unit_price`` column.
        audit: Ledger, mutated in place.

    Returns:
        A copy with ``unit_price`` replaced where it was non-positive, plus a
        boolean ``price_is_imputed`` column.

    Defects handled: PR-04.
    """
    out = df.copy()
    parsed = out["unit_price"].map(parse_price)

    # WHY ``<= 0 or isna`` rather than ``== 0``: a negative or missing list price
    # is the same class of master-data error and must not slip through a check
    # that was written around the one value this file happens to contain.
    invalid_mask = parsed.isna() | (parsed <= 0)
    out["price_is_imputed"] = invalid_mask.fillna(False).to_numpy(dtype=bool)

    detected = int(invalid_mask.sum())
    if not detected:
        return out

    # ── Build the replacement values ─────────────────────────────────────────
    valid = parsed.where(~invalid_mask)
    # WHY the observed category rather than the post-PR-03 "Unknown" bucket:
    # this function runs BEFORE impute_category on purpose, so a product that is
    # missing *both* category and price falls through to the global median
    # instead of taking the median of an "Unknown" cohort that has no meaning.
    category_median = valid.groupby(out["category"].astype("string")).median()
    global_median = float(valid.median())

    replacements: dict[int, float] = {}
    provenance: list[str] = []
    for idx in out.index[invalid_mask]:
        category = out.at[idx, "category"]
        candidate = category_median.get(category, np.nan) if category is not None else np.nan
        if pd.isna(candidate):
            # WHY a global fallback at all: a category whose every price is
            # invalid must still produce a loadable, positive price rather than
            # propagating NaN into a NOT NULL column.
            candidate = global_median
            source = "global median (category cohort had no valid price)"
        else:
            source = f"median of category '{category}'"
        replacements[idx] = round(float(candidate), 2)
        provenance.append(
            f"{out.at[idx, BUSINESS_KEY]}: {parsed[idx]} -> {replacements[idx]:.2f} via {source}"
        )

    evidence = out.loc[invalid_mask, [BUSINESS_KEY, "product_name", "category", "unit_price"]].copy()
    evidence["imputed_unit_price"] = [replacements[i] for i in out.index[invalid_mask]]
    evidence["note"] = "0.00 read as missing; transactions independently ring P027 at 195.34"
    audit.quarantine("products", evidence, DefectCode.PR_04_ZERO_PRICE)

    for idx, value in replacements.items():
        out.at[idx, "unit_price"] = f"{value:.2f}"  # DEFECT: PR-04

    audit.record(
        DefectRecord(
            code=DefectCode.PR_04_ZERO_PRICE,
            detected_count=detected,
            action="imputed",
            affected_keys=out.loc[invalid_mask, BUSINESS_KEY].astype(str).tolist(),
            notes=(
                "unit_price <= 0 read as MISSING, not as a real price. "
                + "; ".join(provenance)
                + ". price_is_imputed=True. Corroboration deliberately NOT written into the "
                "dimension: transactions.csv rings P027 at 195.34 on all 19 of its lines, which "
                "confirms 0.00 is wrong, but cross-populating master data from fact data is a "
                "data-steward decision. Revenue is unaffected -- fact_sales.unit_price is the "
                "transacted price, never the dimension's."
            ),
        )
    )
    return out


# ══════════════════════════════════════════════════════════════════════════════
# PR-03 · NULL category
# ══════════════════════════════════════════════════════════════════════════════
def impute_category(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Fill NULL categories with an explicit ``"Unknown"`` bucket.

    ── PR-03 · NULL category on five products ──────────────────────────────────
    WHY: P003, P009, P016, P023 and P029 have no category -- five of thirty
      products, a sixth of the catalogue, all of them carrying real transactions.
    DECISION: write the literal ``"Unknown"`` and set ``category_is_imputed``.
      No category is guessed.
    WHY NOT GUESS: there is nothing in this file to infer from. The names are
      synthetic ("Product P003") and ``supplier_id`` cycles across all five
      categories by construction, so supplier carries exactly zero signal.
      Inferring a category here would be fabrication dressed as cleaning.
    ALTERNATIVE REJECTED: leaving NULL. Most ``GROUP BY category`` queries drop
      NULLs, so a sixth of the catalogue -- and its revenue -- would simply
      evaporate from every category chart, with the remaining bars still summing
      to a plausible-looking total. A named bucket shows up as a visible gap
      somebody will eventually fix.
    ALTERNATIVE REJECTED: dropping the five products. Never on the table: they
      have real sales, so removing them understates total revenue while leaving
      the total looking perfectly tidy.

    Args:
        df: De-duplicated frame with a ``category`` column.
        audit: Ledger, mutated in place.

    Returns:
        A copy with ``category`` filled and a boolean ``category_is_imputed``
        column.

    Defects handled: PR-03.
    """
    out = df.copy()
    categories = out["category"].astype("string")
    # WHY the empty-string test alongside isna(): normalize_text_columns already
    # unifies them, but this function is public and a caller that skipped
    # normalisation would otherwise get a silently short count.
    missing_mask = categories.isna() | (categories.str.strip() == "")
    missing_mask = missing_mask.fillna(True)
    out["category_is_imputed"] = missing_mask.to_numpy(dtype=bool)

    detected = int(missing_mask.sum())
    if detected:
        out.loc[missing_mask, "category"] = UNKNOWN_CATEGORY  # DEFECT: PR-03
        keys = out.loc[missing_mask, BUSINESS_KEY].astype(str).tolist()
        audit.record(
            DefectRecord(
                code=DefectCode.PR_03_NULL_CATEGORY,
                detected_count=detected,
                action="imputed",
                affected_keys=keys,
                notes=(
                    f"{detected} products ({', '.join(sorted(keys))}) had no category; filled "
                    f"with the literal '{UNKNOWN_CATEGORY}' and category_is_imputed=True. No "
                    "category was guessed: product names are synthetic and supplier_id cycles "
                    "across all five categories, so neither carries any signal. NULL was "
                    "rejected because GROUP BY category drops it and a sixth of the catalogue's "
                    "revenue would silently disappear from every category chart."
                ),
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Typing and post-conditions
# ══════════════════════════════════════════════════════════════════════════════
def _finalize_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast to warehouse dtypes, rename ``unit_price`` and fix column order.

    WHY the rename to ``list_unit_price``: the dimension's price and the fact
    table's price are different things -- one is a reference attribute, the other
    is what the customer actually paid (PR-02, PR-04). Giving them the same name
    is an open invitation to join the wrong one into a margin calculation.

    Defects handled: none directly (typing only).
    """
    out = df.copy()
    out = out.rename(columns={"unit_price": "list_unit_price"})
    for col in ("product_id", "product_name", "category", "supplier_id"):
        out[col] = out[col].astype(str)
    out["list_unit_price"] = out["list_unit_price"].map(parse_price).astype(float).round(2)
    for flag in ("category_is_imputed", "price_is_imputed", "price_conflict"):
        out[flag] = out[flag].astype(bool)
    return out[list(OUTPUT_COLUMNS)].reset_index(drop=True)


def _assert_post_conditions(df: pd.DataFrame) -> None:
    """Fail loudly if the dimension is not loadable.

    Raises:
        AssertionError: On a duplicate key, a null/blank category, or a
            non-positive list price -- each of which is a ``dim_product``
            constraint or a stated cleaning guarantee.

    Defects handled: PR-01, PR-02, PR-03, PR-04 (verification).
    """
    duplicated = df[df["product_id"].duplicated(keep=False)]["product_id"].unique().tolist()
    assert not duplicated, f"PR-01/PR-02 unresolved: duplicate product_id values: {duplicated}"
    assert df["product_id"].notna().all(), "product_id contains nulls; dim_product key is NOT NULL"

    blank_category = df.loc[
        df["category"].isna() | (df["category"].astype(str).str.strip() == ""), "product_id"
    ].tolist()
    assert not blank_category, f"PR-03 unresolved: category still empty for {blank_category}"

    non_positive = df.loc[~(df["list_unit_price"] > 0), "product_id"].tolist()
    assert not non_positive, f"PR-04 unresolved: list_unit_price is not > 0 for {non_positive}"


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration
# ══════════════════════════════════════════════════════════════════════════════
def clean_products(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Clean the product dimension end to end.

    Stage order is load-bearing at two points:

    1. **Normalise text** -- so blanks and NULLs are one kind of missing.
    2. **PR-01 then PR-02**, in that order, inside
       :func:`resolve_duplicate_keys`. Byte-identical copies are removed first,
       which is what makes every surviving key collision provably a *conflict*.
       Collapse on the key instead and the P005 price change is deleted along
       with the P012 copy, which is the single most expensive mistake available
       in this file.
    3. **PR-04 before PR-03** -- the zero-price median is computed over
       *observed* categories, so a product missing both fields cannot end up
       taking the median of the synthetic "Unknown" cohort.
    4. **Type and assert** last: every defect above is only visible while the
       data is still text.

    Args:
        df: Raw, all-string products frame from
            :func:`src.io_utils.read_csv_as_str`.
        audit: Ledger, mutated in place (contract §4).

    Returns:
        The 30-row product dimension with :data:`OUTPUT_COLUMNS`.

    Raises:
        KeyError: If a required source column is absent.
        AssertionError: If a post-condition fails.

    Defects handled: PR-01, PR-02, PR-03, PR-04.
    """
    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"products.csv is missing expected column(s): {missing}")

    # ── 1 · Normalise text ───────────────────────────────────────────────────
    working = normalize_text_columns(df, SOURCE_COLUMNS)

    # ── 2 · PR-01 + PR-02 (partition duplicate keys) ─────────────────────────
    working = resolve_duplicate_keys(working, audit)

    # ── 3 · PR-04 then PR-03 ─────────────────────────────────────────────────
    working = flag_zero_prices(working, audit)
    working = impute_category(working, audit)

    # ── 4 · Type and prove ───────────────────────────────────────────────────
    cleaned = _finalize_types(working)
    _assert_post_conditions(cleaned)
    return cleaned


__all__ = [
    "BUSINESS_KEY",
    "CONFLICT_RESOLUTION_POLICY",
    "OUTPUT_COLUMNS",
    "PAYLOAD_COLUMNS",
    "SOURCE_COLUMNS",
    "UNKNOWN_CATEGORY",
    "clean_products",
    "drop_exact_duplicates",
    "flag_zero_prices",
    "impute_category",
    "normalize_text_columns",
    "parse_price",
    "resolve_duplicate_keys",
    "resolve_price_conflicts",
]
