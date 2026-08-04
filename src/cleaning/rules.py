"""Pure parsing and reconciliation primitives shared by the cleaning layer.

Why this module exists
----------------------
Every type coercion in this pipeline is a decision, and every decision has a
failure mode that must be *visible*. ``src/io_utils.read_csv_as_str`` deliberately
hands the cleaners a frame of raw strings so that nothing is converted by
accident; this module is where those strings become typed values, deliberately,
in functions that are small enough to reason about and to unit-test in isolation.

Nothing here touches a DataFrame's identity, a config singleton, the filesystem,
the clock or the audit log. Every function is a **pure** mapping from inputs to a
value or a frozen result object. That is not stylistic tidiness -- it is what
makes the four hard decisions in this file arguable:

* :func:`parse_date_multiformat` -- TX-01. Which of three date formats a string
  is in, and whether the answer is genuinely knowable.
* :func:`parse_currency`         -- TX-02. What ``"$142.50"`` is worth, and what
  happens to a value that refuses to parse (answer: it becomes ``None``, never
  ``0.0``).
* :func:`coerce_int`             -- quantity, including the negative quantities
  that TX-10 returns depend on.
* :func:`reconcile_line_amount`  -- TX-03. The single most important function in
  the project: it computes the gap between the reported total and the derived
  one *without ever changing the reported total*.

Defect codes owned (as the mechanism; the decisions are recorded by
``src/cleaning/transactions.py``):

* **TX-01** mixed date formats  -- :func:`parse_date_multiformat`
                                   (alias :func:`parse_transaction_date`)
* **TX-02** string currency     -- :func:`parse_currency`
* **TX-03** silent discount     -- :func:`reconcile_line_amount`
* **TX-10** returns             -- :func:`coerce_int` must preserve the minus sign

Inputs:  scalar strings (or ``None``/``NaN``) as read from CSV, plus pandas
         Series for the vectorised convenience wrappers.
Outputs: typed scalars and frozen result records. No side effects of any kind.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any, Final, Mapping

import pandas as pd

from src.config import DATE_FORMATS, PRICE_TOLERANCE

# ── Money precision ───────────────────────────────────────────────────────────
# WHY a named constant rather than a literal 2 sprinkled through the file: the
# reconciliation tolerance (config.PRICE_TOLERANCE = $0.01) and the rounding
# precision are two halves of the same decision. If money ever moves to 3dp
# (FX, unit costs) both must move together, and a reader must be able to see
# that they are coupled.
MONEY_DECIMAL_PLACES: Final[int] = 2

# ── Textual sentinels that are NOT numbers ────────────────────────────────────
# WHY this exists: ``float("nan")`` and ``float("inf")`` both succeed. A CSV cell
# containing the literal text "nan" would therefore parse to a float and sail
# through every downstream check as a number, poisoning every sum it touches.
# The guard is cheap; the failure it prevents is silent and total.
_NON_NUMERIC_TOKENS: Final[frozenset[str]] = frozenset(
    {"", "-", "+", ".", "nan", "none", "null", "na", "n/a", "#n/a", "inf", "-inf", "infinity"}
)

# ── What a "clean" numeric string looks like ──────────────────────────────────
# WHY anchored and deliberately narrow: this regex is the *detector* for TX-02,
# not the parser. Anything it rejects is, by definition, a value that a naive
# ``astype(float)`` would have thrown on and that ``errors="coerce"`` would have
# silently turned into NaN. Widening it to be "helpful" would shrink the number
# of defects the pipeline can prove it found.
_BARE_DECIMAL_RE: Final[re.Pattern[str]] = re.compile(r"^[+-]?\d+(?:\.\d+)?$")

# Characters stripped before a numeric parse is attempted. Order matters only in
# that every one of these is *presentation*, never value.
_CURRENCY_NOISE: Final[tuple[str, ...]] = ("$", "USD", "usd", ",", " ", " ", "\t")

# ── TX-01: the day/month ambiguity partners ───────────────────────────────────
# WHY this map is here at all, given that neither partner is in config.DATE_FORMATS:
#
#   The two injected formats are '%m/%d/%Y' (US) and '%d-%m-%Y' (EU). Consider the
#   string "03-05-2026". Under '%d-%m-%Y' it is 3 May 2026. Under '%m-%d-%Y' it is
#   5 March 2026. **Both are valid calendar dates and nothing in the string itself
#   can distinguish them.** The same trap exists on the '/' side: "05/03/2026" is
#   5 March under the US format and 3 May under the EU one.
#
#   Whenever the leading component is <= 12, the two readings are both legal and
#   the value is *genuinely ambiguous as data*. It is only resolvable from
#   provenance -- here, scripts/seed_data.py lines 160-165, which writes rows 0-9
#   with strftime('%m/%d/%Y') and rows 10-19 with strftime('%d-%m-%Y'). The
#   separator is what carries that provenance: '/' means the US rewrite, '-' with
#   a two-digit head means the EU rewrite, and a four-digit head means the
#   untouched ISO original. So no string can match two rungs of the ladder, and
#   the ORDER of config.DATE_FORMATS is the tie-break rule rather than a comment.
#
#   This map exists so the parser can *quantify* how much of that resolution rests
#   on provenance rather than on the data: for every row it reports whether the
#   day/month-swapped reading would also have been valid. The audit log publishes
#   that count. Silently picking a reading and not saying how many were coin-flips
#   is precisely the kind of undeclared assumption this project is arguing against.
_DAY_MONTH_SWAP: Final[Mapping[str, str]] = {
    "%m/%d/%Y": "%d/%m/%Y",
    "%d-%m-%Y": "%m-%d-%Y",
    "%m-%d-%Y": "%d-%m-%Y",
    "%d/%m/%Y": "%m/%d/%Y",
}

ISO_DATE_FORMAT: Final[str] = "%Y-%m-%d"
"""The one unambiguous rung. A four-digit leading component cannot be a day or a
month, so ISO strings are safe to attempt first and can never be stolen by a
later format."""


# ── Result records ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DateParseResult:
    """Outcome of one attempt to read a date string against an ordered ladder.

    WHY a record instead of returning a bare Timestamp: the *format that matched*
    is the TX-01 evidence. A function that returns only the date can tell you
    that parsing succeeded but not that 20 rows needed a non-ISO rung, which is
    the finding. And ``ambiguous_with`` is the honesty field -- it records that a
    different, equally valid reading existed and was rejected on provenance.

    Attributes:
        value: Parsed date, or ``None`` if no format in the ladder matched.
        matched_format: The strftime pattern that succeeded, or ``None``.
        raw: The input exactly as received, so a failure can be reported with
            the offending text rather than just a row number.
        is_iso: True when the ISO rung matched -- i.e. the row was already clean.
        ambiguous_with: The day/month-swapped format that would *also* have
            parsed this string to a different valid date, or ``None`` when the
            reading is structurally forced (e.g. a leading component > 12).
        alternate_value: The date that swapped reading would have produced.
            Carried so the audit note can show a concrete example rather than
            assert the ambiguity abstractly.
    """

    value: dt.date | None
    matched_format: str | None
    raw: str
    is_iso: bool = False
    ambiguous_with: str | None = None
    alternate_value: dt.date | None = None

    @property
    def ok(self) -> bool:
        """True when some rung of the ladder matched."""
        return self.value is not None

    @property
    def needed_non_iso_format(self) -> bool:
        """True for exactly the TX-01 rows: parsed, but not by the ISO rung."""
        return self.ok and not self.is_iso

    @property
    def is_ambiguous(self) -> bool:
        """True when a day/month swap would also have yielded a valid date."""
        return self.ambiguous_with is not None


@dataclass(frozen=True)
class LineReconciliation:
    """Outcome of comparing a reported line total against its derived value.

    WHY every field is kept rather than just the boolean: the boolean answers
    "was there a discount", but the reconciliation metric has to *tie out* --
    gross list value minus discount must equal net revenue to the cent. That
    needs the numbers, per line, not a flag.

    Attributes:
        extended_amount: ``quantity * unit_price`` -- the list value. A
            derivation, never a fact.
        total_amount: The reported total, passed through **unmodified**. This is
            the fact, and it is authoritative for revenue.
        discount_amount: ``extended_amount - total_amount``. Positive means the
            customer paid less than list (a discount); negative would mean they
            paid more (a surcharge or a data error), and the sign is preserved
            so the two cases stay distinguishable.
        has_discount: ``abs(discount_amount) > tolerance``.
        discount_pct: Discount as a fraction of ``extended_amount``, or ``None``
            when the list value is zero (no meaningful denominator).
    """

    extended_amount: float
    total_amount: float
    discount_amount: float
    has_discount: bool
    discount_pct: float | None


# ── Small shared helpers ──────────────────────────────────────────────────────
def is_blank(value: Any) -> bool:
    """True when ``value`` carries no information at all.

    Args:
        value: Anything arriving from a ``dtype=str`` DataFrame cell.

    Returns:
        True for ``None``, ``NaN``/``NaT``/``pd.NA``, the empty string, and
        whitespace-only strings.

    Defects handled: none directly -- but TX-06 (NULL customer_id) is defined by
        this predicate, and reading blanks as blanks rather than as the literal
        text "nan" is what keeps that count at 40 instead of 0.
    """
    if value is None:
        return True
    # WHY pd.isna and not ``value != value``: pandas hands back np.nan, pd.NaT
    # and pd.NA depending on the column, and only pd.isna recognises all three.
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return True
    except (TypeError, ValueError):  # pragma: no cover - non-scalar input
        return False
    return isinstance(value, str) and not value.strip()


def normalise_text(value: Any) -> str | None:
    """Trim surrounding whitespace, mapping blanks to ``None``.

    WHY trimming happens here and not in the CSV reader: ``read_csv_as_str``
    deliberately does no whitespace handling, because trimming is a cleaning
    decision that should be countable and auditable rather than an invisible
    side effect of reading a file. This is that decision, made once.

    Args:
        value: Raw cell value.

    Returns:
        The stripped string, or ``None`` when the cell is blank.

    Defects handled: none directly (normalisation primitive).
    """
    if is_blank(value):
        return None
    return str(value).strip()


def round_money(value: float) -> float:
    """Round to cents, the currency's actual precision.

    WHY round at all: ``2 * 196.2`` is ``392.40000000000003`` in IEEE-754. Left
    alone, that noise propagates into ``discount_amount`` and a reviewer adding
    up the audit report finds it off by fractions of a cent -- which looks like a
    logic error and costs an hour to disprove.

    Args:
        value: A monetary amount.

    Returns:
        ``value`` rounded to :data:`MONEY_DECIMAL_PLACES`.

    Defects handled: TX-03 (keeps the discount arithmetic exact to the cent).
    """
    return round(float(value), MONEY_DECIMAL_PLACES)


# ══ TX-01 · Date parsing ══════════════════════════════════════════════════════
def parse_date_multiformat(
    value: Any,
    formats: tuple[str, ...] = DATE_FORMATS,
) -> DateParseResult:
    """Parse a date string against an **ordered** ladder of explicit formats.

    This is the TX-01 handler. It exists because the obvious one-liner is wrong
    in two different ways:

    * ``pd.to_datetime(col, errors="coerce")`` NaTs every string it cannot infer.
      On this file that silently deletes the 20 non-ISO rows -- real transactions,
      real revenue -- and leaves a column that looks fine.
    * ``pd.to_datetime(col, dayfirst=True)`` (or ``False``) applies **one** guess
      to a column that contains **three** formats. Ten rows are then misparsed
      rather than dropped, which is worse: ``"03-05-2026"`` becomes 5 March when
      the source meant 3 May, and the output is complete, plausible and false.

    The ladder works here because the three formats are structurally separable::

        "2026-05-30"  four-digit head           -> ISO, cannot be a day or month
        "05/30/2026"  '/' separator             -> the US rewrite (rows 0-9)
        "30-05-2026"  '-' with two-digit head   -> the EU rewrite (rows 10-19)

    No string can match two rungs, so the order is a tie-break that never has to
    fire -- but it is still the stated rule, which is why
    :data:`src.config.DATE_FORMATS` is an ordered tuple and not a set.

    On the residual ambiguity, stated plainly: for any value whose leading
    component is <= 12 the day/month reading is *not* recoverable from the string.
    ``"03-05-2026"`` is a valid 3 May and a valid 5 March. This parser resolves it
    from provenance (the separator identifies which rewrite produced it) and then
    **reports** how many rows relied on that resolution via
    :attr:`DateParseResult.ambiguous_with`, so the assumption is a published
    number rather than a silent choice.

    Args:
        value: Raw cell value; blanks yield an unparsed result rather than raising.
        formats: Ordered strftime patterns to attempt. Defaults to
            :data:`src.config.DATE_FORMATS`.

    Returns:
        A :class:`DateParseResult`. ``value is None`` means *no rung matched* --
        the caller must quarantine the row, never coerce it.

    Raises:
        Nothing. WHY: a parse failure is data, not a program error. Raising here
        would abort a 505-row run over one bad cell and lose the other 504
        findings; returning an unparsed result lets the cleaner quarantine the
        row and carry on with the loss on the books.

    Defects handled: TX-01.
    """
    raw = normalise_text(value)
    if raw is None:
        return DateParseResult(value=None, matched_format=None, raw="")

    for fmt in formats:
        try:
            parsed = dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            # WHY ValueError only: strptime raises it for both "wrong shape" and
            # "impossible calendar date" (e.g. day 2026). Both mean "this rung
            # does not apply", so both must fall through to the next rung.
            continue

        # ── Ambiguity probe (reporting only -- never changes the answer) ──────
        # WHY probe a format that is deliberately NOT in the ladder: the question
        # is not "could another rung have matched" (none can, by construction) but
        # "is the value ambiguous as data". Swapping day and month answers that.
        swapped_fmt = _DAY_MONTH_SWAP.get(fmt)
        alternate: dt.date | None = None
        if swapped_fmt is not None:
            try:
                candidate = dt.datetime.strptime(raw, swapped_fmt).date()
            except ValueError:
                candidate = None  # leading component > 12: the reading is forced
            # WHY require a *different* date: "05-05-2026" parses identically
            # under both readings, so it is not actually ambiguous.
            if candidate is not None and candidate != parsed:
                alternate = candidate

        return DateParseResult(
            value=parsed,
            matched_format=fmt,
            raw=raw,
            is_iso=(fmt == ISO_DATE_FORMAT),
            ambiguous_with=(swapped_fmt if alternate is not None else None),
            alternate_value=alternate,
        )

    # Every rung failed. Report it; do not invent a date.
    return DateParseResult(value=None, matched_format=None, raw=raw)


def parse_transaction_date(
    value: Any,
    formats: tuple[str, ...] = DATE_FORMATS,
) -> DateParseResult:
    """Alias for :func:`parse_date_multiformat`.

    WHY the alias exists: ``DEFECT_CATALOG[TX-01].source_ref`` points at
    ``src/cleaning/rules.py:parse_transaction_date``, and a reviewer who follows
    that reference must land on a real symbol. Keeping the alias is cheaper and
    more honest than editing another agent's published catalog entry.

    Args:
        value: Raw cell value.
        formats: Ordered strftime patterns to attempt.

    Returns:
        A :class:`DateParseResult`.

    Defects handled: TX-01.
    """
    return parse_date_multiformat(value, formats)


def parse_date_series(
    series: pd.Series,
    formats: tuple[str, ...] = DATE_FORMATS,
) -> pd.DataFrame:
    """Apply the format ladder across a column, returning parse evidence.

    WHY ``.map`` of the scalar function rather than a vectorised ladder of
    ``pd.to_datetime(subset, format=fmt)`` calls: the vectorised version is
    faster but it is a *second* implementation of the same rule, and the unit
    tests would then be testing the scalar one while the pipeline ran the other.
    At 505 rows the elementwise cost is unmeasurable, and "the function under
    test is literally the function that runs" is worth far more than the
    milliseconds. If this file ever reaches millions of rows, the honest upgrade
    is to vectorise *and* assert equivalence against the scalar path on a sample.

    Args:
        series: Column of raw date strings.
        formats: Ordered strftime patterns to attempt.

    Returns:
        A DataFrame aligned to ``series.index`` with columns:
        ``parsed_date`` (datetime64[ns], NaT when unparsed), ``matched_format``,
        ``is_iso``, ``needed_non_iso_format``, ``is_ambiguous``,
        ``ambiguous_with``, ``alternate_date``.

    Defects handled: TX-01.
    """
    results = [parse_date_multiformat(v, formats) for v in series]
    return pd.DataFrame(
        {
            "parsed_date": pd.to_datetime(
                pd.Series([r.value for r in results], index=series.index)
            ),
            "matched_format": [r.matched_format for r in results],
            "is_iso": [r.is_iso for r in results],
            "needed_non_iso_format": [r.needed_non_iso_format for r in results],
            "is_ambiguous": [r.is_ambiguous for r in results],
            "ambiguous_with": [r.ambiguous_with for r in results],
            "alternate_date": pd.to_datetime(
                pd.Series([r.alternate_value for r in results], index=series.index)
            ),
        },
        index=series.index,
    )


# ══ TX-02 · Currency parsing ══════════════════════════════════════════════════
def is_currency_formatted(value: Any) -> bool:
    """True when a cell is a number wearing presentation clothing.

    This is the TX-02 *detector*, kept deliberately separate from the parser.
    A blank is not "currency formatted" (it is missing, which is a different
    defect) and a bare decimal is not either. Everything else -- ``"$142.50"``,
    ``"1,234.00"``, ``" 12.00 "``, ``"(75.00)"`` -- is a value that
    ``astype(float)`` would have raised on.

    Args:
        value: Raw cell value.

    Returns:
        True when the cell is non-blank and does not match
        ``^[+-]?\\d+(\\.\\d+)?$``.

    Defects handled: TX-02.
    """
    text = normalise_text(value)
    if text is None:
        return False
    return _BARE_DECIMAL_RE.match(text) is None


def parse_currency(value: Any) -> float | None:
    """Turn a money string into a float, or into ``None`` -- never into zero.

    Handles the four presentations that appear in, or plausibly appear next to,
    this dataset: a currency symbol (``"$142.50"``), thousands separators
    (``"1,234.56"``), padding whitespace, and accounting negatives
    (``"(142.50)"`` meaning ``-142.50``).

    The important behaviour is the failure path. ``pd.to_numeric(errors="coerce")``
    followed by ``.fillna(0)`` is the idiom this function exists to replace: on
    this file it would understate revenue by roughly $3.5k while producing a
    perfectly clean float column that no schema check and no test would flag.
    Returning ``None`` forces the caller to make an explicit decision -- in this
    pipeline, to quarantine the row so the loss stays on the books.

    Args:
        value: Raw cell value.

    Returns:
        The amount as a float, or ``None`` when the cell is blank or the text
        cannot be read as a finite number.

    Defects handled: TX-02, and TX-10 by preserving a leading minus sign so a
        return stays a credit rather than becoming a sale.
    """
    text = normalise_text(value)
    if text is None:
        return None

    # ── Accounting negatives ─────────────────────────────────────────────────
    # WHY handle these at all when the file has none: a finance extract that
    # gains one later would otherwise parse "(142.50)" as None, quarantine a
    # perfectly good refund, and look like a pipeline bug rather than a format
    # change. The branch is three lines and removes a whole class of surprise.
    negated_by_parentheses = text.startswith("(") and text.endswith(")")
    if negated_by_parentheses:
        text = text[1:-1].strip()

    # WHY strip rather than regex-extract: extraction with a permissive pattern
    # would happily pull "142" out of "142 units", inventing a number from a
    # cell that was never money. Stripping known presentation characters and
    # then demanding that the remainder parse cleanly cannot do that.
    for noise in _CURRENCY_NOISE:
        text = text.replace(noise, "")
    text = text.replace("−", "-")  # UTF-8 MINUS SIGN, common in exported PDFs
    if text.startswith("+"):
        text = text[1:]

    if text.lower() in _NON_NUMERIC_TOKENS:
        return None  # see _NON_NUMERIC_TOKENS: float("nan") would have succeeded

    try:
        amount = float(text)
    except ValueError:
        return None
    if not math.isfinite(amount):  # belt and braces after the token guard
        return None

    if negated_by_parentheses:
        # WHY -abs() rather than a plain negation: "(-5)" is pathological input,
        # and a plain negation would silently turn it positive. -abs() makes the
        # bracket notation mean exactly one thing -- "this is a credit".
        amount = -abs(amount)
    return round_money(amount)


def parse_currency_series(series: pd.Series) -> pd.DataFrame:
    """Parse a money column, returning both the values and the TX-02 evidence.

    Args:
        series: Column of raw amount strings.

    Returns:
        A DataFrame aligned to ``series.index`` with ``amount`` (float64, NaN
        when unparsed), ``was_currency_formatted`` (the TX-02 flag) and
        ``parse_failed``.

    Defects handled: TX-02.
    """
    formatted = [is_currency_formatted(v) for v in series]
    values = [parse_currency(v) for v in series]
    blanks = [is_blank(v) for v in series]
    return pd.DataFrame(
        {
            "amount": pd.Series(values, index=series.index, dtype="float64"),
            "was_currency_formatted": pd.Series(formatted, index=series.index, dtype=bool),
            # WHY blank is not a parse failure: a missing amount and an
            # unreadable amount are different problems with different owners.
            # Collapsing them would make the quarantine file uninterpretable.
            "parse_failed": pd.Series(
                [v is None and not b for v, b in zip(values, blanks)],
                index=series.index,
                dtype=bool,
            ),
        },
        index=series.index,
    )


# ══ Integer coercion ══════════════════════════════════════════════════════════
def coerce_int(value: Any) -> int | None:
    """Read an integer, refusing to guess.

    Two behaviours are deliberate and worth arguing about:

    * **Negatives pass through untouched.** The 30 TX-10 return rows carry
      ``quantity = -3`` and the like. Any "sanitisation" here -- ``abs()``, a
      ``max(0, n)`` clamp, a ``> 0`` filter -- converts a refund into a sale,
      which is the worst available sign error: it moves net revenue by twice the
      value of the return, in the wrong direction.
    * **A non-integral float is a failure, not a truncation.** ``"2.7"`` returns
      ``None`` rather than ``2``. Silently truncating a quantity loses value with
      no trace; returning ``None`` forces the caller to quarantine and count it.
      ``"3.0"`` *is* accepted, because that is an integer that has been through a
      float column on its way to the CSV, not a fractional quantity.

    Args:
        value: Raw cell value.

    Returns:
        The integer, or ``None`` for blanks and anything not exactly integral.

    Defects handled: TX-07 (zero quantity must survive parsing to be counted, so
        ``0`` is a perfectly valid result here), TX-10 (sign preservation).
    """
    text = normalise_text(value)
    if text is None:
        return None
    text = text.replace(",", "").replace("−", "-")
    if text.lower() in _NON_NUMERIC_TOKENS:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        as_float = float(text)
    except ValueError:
        return None
    if not math.isfinite(as_float) or not float(as_float).is_integer():
        return None
    return int(as_float)


def coerce_int_series(series: pd.Series) -> pd.DataFrame:
    """Coerce an integer column, returning the values and a failure flag.

    Args:
        series: Column of raw integer strings.

    Returns:
        A DataFrame aligned to ``series.index`` with ``value`` (nullable
        ``Int64``, so a failure stays distinguishable from a legitimate ``0``)
        and ``parse_failed``.

    Defects handled: TX-07, TX-10.
    """
    values = [coerce_int(v) for v in series]
    blanks = [is_blank(v) for v in series]
    return pd.DataFrame(
        {
            # WHY nullable Int64 and not int64: a plain int64 column cannot hold
            # a missing value, so pandas would upcast the whole column to float
            # and 0 would become 0.0 -- at which point TX-07's "quantity == 0"
            # test is comparing floats, which is exactly the kind of sloppiness
            # this pipeline is meant to avoid.
            "value": pd.Series(values, index=series.index, dtype="Int64"),
            "parse_failed": pd.Series(
                [v is None and not b for v, b in zip(values, blanks)],
                index=series.index,
                dtype=bool,
            ),
        },
        index=series.index,
    )


# ══ TX-03 · Line reconciliation ═══════════════════════════════════════════════
def reconcile_line_amount(
    quantity: int | float,
    unit_price: float,
    total_amount: float,
    tolerance: float = PRICE_TOLERANCE,
) -> LineReconciliation:
    """Compare a reported line total against ``quantity * unit_price``.

    **This function never returns a corrected total, because there is nothing to
    correct.** ``total_amount`` is what the source system says the customer was
    charged; ``quantity * unit_price`` is a derivation from two other columns.
    When they disagree, the fact wins and the derivation is the thing that needs
    explaining. Writing ``total_amount = quantity * unit_price`` -- which the
    previous attempt did at ``cleaner.py:116`` -- inverts the direction of truth,
    overstates revenue by the entire discount pool, and destroys the evidence
    that a discount ever happened, so the finding can never be reported.

    What the gap actually means: an unmodelled promotion, a manual price override
    or a loyalty adjustment is flowing through a schema that has nowhere to
    record it. Surfacing ``discount_amount`` is what turns a reconciliation break
    into a business finding.

    Args:
        quantity: Units sold (negative for a return).
        unit_price: Price as transacted, per unit.
        total_amount: The amount reported by the source. Returned unmodified.
        tolerance: Dollar threshold above which a gap counts as real. Defaults to
            :data:`src.config.PRICE_TOLERANCE` ($0.01). WHY a tolerance rather
            than ``!=``: money is rounded to cents and floats do not represent
            cents exactly, so ``2 * 196.2 != 392.4`` is True in IEEE-754 and an
            exact comparison would flag hundreds of clean rows. The seeded
            discounts are 5-20% of order value, so a one-cent floor separates
            signal from float noise with an enormous margin.

    Returns:
        A :class:`LineReconciliation`.

    Defects handled: TX-03.
    """
    extended = round_money(float(quantity) * float(unit_price))
    reported = round_money(float(total_amount))
    # DEFECT: TX-03  <- the gap is *exposed*, never closed by rewriting `reported`
    discount = round_money(extended - reported)
    return LineReconciliation(
        extended_amount=extended,
        total_amount=reported,
        discount_amount=discount,
        # WHY abs(): a negative gap (customer charged MORE than list) is just as
        # much a reconciliation break as a discount, and must not be swallowed by
        # a one-sided ``discount > tolerance`` test.
        has_discount=abs(discount) > float(tolerance),
        # WHY guard on extended rather than on discount: a zero-quantity line has
        # a zero list value, and 0/0 is not "a 0% discount", it is undefined.
        discount_pct=(None if extended == 0 else round(discount / extended, 6)),
    )


__all__ = [
    "DateParseResult",
    "ISO_DATE_FORMAT",
    "LineReconciliation",
    "MONEY_DECIMAL_PLACES",
    "coerce_int",
    "coerce_int_series",
    "is_blank",
    "is_currency_formatted",
    "normalise_text",
    "parse_currency",
    "parse_currency_series",
    "parse_date_multiformat",
    "parse_date_series",
    "parse_transaction_date",
    "reconcile_line_amount",
    "round_money",
]
