"""Composable, dataset-agnostic check primitives used by the profiler and cleaners.

Everything in this module obeys three rules, and the rules are the point:

1. **It observes; it never decides.** Nothing here drops, imputes, coerces or
   rewrites a value. Each function answers a question ("does this parse?", "is
   this key unique?", "does this arithmetic hold?") and returns evidence. The
   *decision* about what to do with that evidence belongs to
   ``src/cleaning/*``, where it can be audited against
   :data:`src.defects.DEFECT_CATALOG`.
2. **It knows nothing about this dataset.** No column names, no store IDs, no
   defect codes are hard-coded here. Every function takes the column(s) it works
   on as an argument, which is what lets the same profiler run unchanged over
   stores, products and transactions -- and over the next three files someone
   points it at.
3. **It is total.** Empty frames, all-null columns, single-row frames and
   all-blank columns return a well-formed report rather than raising. A checker
   that explodes on the degenerate case is a checker nobody runs in CI.

Defect codes owned: none. Deliberately -- see rule 1. These primitives make
several defect classes *visible*, and the module notes where in prose, but the
``# DEFECT: <CODE>`` tags belong on the lines that handle them in the cleaning
layer, not on the lines that merely measure.

Inputs:  string-typed values and DataFrames as produced by
         :func:`src.io_utils.read_csv_as_str`.
Outputs: plain dicts and scalars, all JSON-safe.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from src.config import DATE_FORMATS, PRICE_TOLERANCE

# ── Regex vocabulary ──────────────────────────────────────────────────────────
# WHY regexes rather than try/except around int()/float(): Python's built-ins are
# far more permissive than a CSV field has any right to be. ``int("1_000")`` is
# 1000, ``float("nan")`` and ``float("Infinity")`` both succeed, and
# ``float(" 1e10 ")`` succeeds too. Every one of those would let a value that is
# *not* a well-formed number in the source pass a "is this parseable" check, and
# the whole premise of this project is that we do not let bad values pass
# quietly. An explicit grammar is the only way to be strict on purpose.
_INT_RE: re.Pattern[str] = re.compile(r"^[+-]?\d+$")
_DECIMAL_RE: re.Pattern[str] = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")

CURRENCY_SYMBOLS: str = "$€£¥₹"
"""Symbols stripped by :func:`strip_currency`. Kept as data so a new market's
symbol is a one-character change rather than a code change."""

_CURRENCY_MARKER_RE: re.Pattern[str] = re.compile(rf"[{re.escape(CURRENCY_SYMBOLS)},]")
"""A value "looks like currency" if it carries a symbol or a thousands comma.
WHY the comma counts: ``"1,250.00"`` is unmistakably formatted money even
without a symbol, and it is exactly the form that ``float()`` rejects."""

_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[A-Za-z][A-Za-z0-9]*[-_]?\d+$")
"""Shape of a synthetic business key: a letter-led prefix followed by digits
(``S001``, ``P012``, ``TXN10179``, ``CUST0247``, ``SUP005``). Used only as one
input to type inference, never as a validity rule."""

_BLANK_RE: re.Pattern[str] = re.compile(r"^\s*$")


# ── Scalar normalisation ──────────────────────────────────────────────────────
def is_missing(value: Any) -> bool:
    """True when a value carries no information: NULL, NaN, or whitespace only.

    Args:
        value: Any cell value straight out of a string-typed DataFrame.

    Returns:
        True if the value is None/NaN/NaT, or a string that is empty or all
        whitespace.

    Defects handled: none (primitive). Underpins every null and blank count in
        the profile, which is how ST-03, PR-03 and TX-06 become visible.
    """
    if value is None:
        return True
    # WHY the try: pd.isna() raises on list-likes, and a malformed CSV can in
    # principle deliver one. A checker must not be the thing that crashes.
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    return isinstance(value, str) and bool(_BLANK_RE.match(value))


def is_blank_string(value: Any) -> bool:
    """True only for a present-but-empty string (``""`` or ``"   "``).

    Distinct from :func:`is_missing` on purpose: ``NULL`` and ``""`` mean
    different things in a source system ("not supplied" vs "supplied as
    nothing"), and collapsing them at read time destroys the distinction before
    anyone can decide whether it matters.

    Args:
        value: Cell value.

    Returns:
        True for a string of zero or more whitespace characters; False for NULL.

    Defects handled: none (primitive).
    """
    return isinstance(value, str) and bool(_BLANK_RE.match(value))


def _token(value: Any) -> str | None:
    """Return the trimmed string form of ``value``, or None if it is missing.

    Every parser below starts here, so trimming policy is defined exactly once.

    Args:
        value: Cell value.

    Returns:
        The stripped string, or None when :func:`is_missing` holds.

    Defects handled: none (primitive).
    """
    if is_missing(value):
        return None
    return str(value).strip()


# ── Numeric parseability ──────────────────────────────────────────────────────
def is_parseable_as_int(value: Any) -> bool:
    """True when ``value`` is a well-formed integer literal.

    Thousands separators are tolerated (``"1,250"``); decimal points are not
    (``"1.0"`` is a decimal, not an integer). Missing values return False --
    "absent" is not "parseable", and conflating the two would let a 100%-null
    column masquerade as a clean integer column.

    Args:
        value: Cell value.

    Returns:
        True if the value matches an integer grammar.

    Defects handled: none directly. Applied to ``quantity`` it is what makes
        TX-07's zero rows countable as numbers rather than guessed at.
    """
    token = _token(value)
    if token is None:
        return False
    return bool(_INT_RE.match(token.replace(",", "")))


def is_parseable_as_decimal(value: Any) -> bool:
    """True when ``value`` is a well-formed decimal literal.

    Integers are a subset: ``is_parseable_as_int(v)`` implies this returns True.
    Currency-formatted values are **not** accepted here -- run
    :func:`strip_currency` first. WHY keep them separate: "this column is money
    written as text" is a materially different finding from "this column is a
    number", and merging the two checks would hide it.

    Args:
        value: Cell value.

    Returns:
        True if the value matches a decimal grammar.

    Defects handled: none directly (see :func:`strip_currency` for TX-02).
    """
    token = _token(value)
    if token is None:
        return False
    return bool(_DECIMAL_RE.match(token.replace(",", "")))


def has_currency_marker(value: Any) -> bool:
    """True when ``value`` carries a currency symbol or a thousands separator.

    This is the counter behind the profile's ``currency_marker_count``: on
    ``transactions.total_amount`` it is non-zero, which is precisely the evidence
    that the column is money-as-text and cannot be summed as it stands.

    Args:
        value: Cell value.

    Returns:
        True if a currency symbol or comma is present.

    Defects handled: none (observation only).
    """
    token = _token(value)
    if token is None:
        return False
    return bool(_CURRENCY_MARKER_RE.search(token))


def strip_currency(value: Any) -> str | None:
    """Remove currency formatting, returning the bare numeric text.

    Handles symbol prefixes/suffixes, thousands commas, non-breaking spaces and
    the accountancy convention where parentheses mean negative -- ``"($12.50)"``
    becomes ``"-12.50"``. Returns the *text*, not a float, so the caller decides
    whether the remainder is actually a number (it may not be: ``"$ n/a"``
    strips to ``"n/a"``, which is a finding, not a zero).

    Args:
        value: Cell value.

    Returns:
        The de-formatted string, or None if the value was missing.

    Defects handled: none directly -- but this is the function that makes the 25
        ``"$142.50"``-style amounts in ``transactions.total_amount`` measurable
        instead of silently unsummable.
    """
    token = _token(value)
    if token is None:
        return None
    # WHY strip the unicode non-breaking space explicitly: exports from Excel and
    # from European locales use it as a thousands separator, and str.strip() with
    # no argument does not remove it in every Python build's default whitespace
    # class the way one would hope.
    cleaned = token.replace(" ", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    for symbol in CURRENCY_SYMBOLS:
        cleaned = cleaned.replace(symbol, "")
    cleaned = cleaned.replace(",", "").replace(" ", "").strip()
    if negative and cleaned and not cleaned.startswith("-"):
        cleaned = f"-{cleaned}"
    return cleaned


def to_float(value: Any) -> float | None:
    """Parse a possibly currency-formatted value into a float.

    Args:
        value: Cell value.

    Returns:
        The float, or None if the value is missing or does not match the decimal
        grammar after currency formatting is removed. Never returns NaN --
        callers get None so they cannot accidentally propagate a NaN into an
        aggregate and read the result as a number.

    Defects handled: none (primitive used by the numeric statistics).
    """
    cleaned = strip_currency(value)
    if cleaned is None or not is_parseable_as_decimal(cleaned):
        return None
    return float(cleaned)


def to_float_series(series: pd.Series) -> pd.Series:
    """Vectorised :func:`to_float` over a string column.

    Args:
        series: A column of strings (or NaN).

    Returns:
        A float64 Series with NaN wherever the value did not parse. NaN here
        means "did not parse", and the caller is expected to count those rather
        than ignore them.

    Defects handled: none (primitive).
    """
    if series.empty:
        # WHY an explicit early return: ``Series([], dtype=object).map(...)``
        # keeps dtype object, and downstream ``.mean()`` on an object Series
        # raises rather than returning NaN.
        return pd.Series([], dtype="float64", index=series.index)
    return pd.to_numeric(series.map(to_float), errors="coerce").astype("float64")


# ── Date parseability (this is what makes mixed date formats visible) ─────────
def parseable_date_formats(
    value: Any, formats: Sequence[str] = DATE_FORMATS
) -> tuple[str, ...]:
    """Return **every** format in ``formats`` that ``value`` parses under.

    This function is the reason the profile can show that a date column contains
    three different encodings rather than one. The usual approach --
    ``pd.to_datetime(col, errors="coerce")`` -- returns a single column of
    timestamps and NaT, which answers "did it parse?" but never "under which
    grammar?". Two failure modes hide in that gap:

    * a value that parses under **no** format is silently NaT, and a downstream
      ``dropna()`` deletes the row without anyone counting it;
    * a value that parses under **more than one** format (``03-04-2026`` is both
      a 3 April and a 4 March) is silently assigned one of them, and the answer
      is wrong rather than missing -- which is far worse, because nothing looks
      broken.

    Returning the full match set means both cases appear as numbers in the
    profile: ``unmatched_count`` and ``ambiguous_count``. A reviewer reading
    ``format_histogram`` sees the mixed encodings directly, before any cleaning
    code has had the chance to resolve them one way or the other.

    Args:
        value: Cell value.
        formats: ``strptime`` patterns to try, in priority order. Defaults to
            :data:`src.config.DATE_FORMATS`, whose ordering is itself the
            project's documented disambiguation rule.

    Returns:
        A tuple of the matching format strings, in the order given. Empty when
        the value is missing or matches nothing.

    Defects handled: none (observation). It is the evidence source for the
        mixed-format finding; the resolution lives in the cleaning layer.
    """
    token = _token(value)
    if token is None:
        return ()
    matches: list[str] = []
    for fmt in formats:
        try:
            dt.datetime.strptime(token, fmt)
        except (ValueError, TypeError):
            continue
        matches.append(fmt)
    return tuple(matches)


def parse_date_first_match(
    value: Any, formats: Sequence[str] = DATE_FORMATS
) -> dt.date | None:
    """Parse ``value`` using the first format in ``formats`` that matches.

    Provided for the cleaning layer so that profiling and cleaning agree, by
    construction, on what a date is and which grammar wins a tie. Priority order
    is the tie-break rule; it is data (``config.DATE_FORMATS``), not a literal
    buried in a parser.

    Args:
        value: Cell value.
        formats: ``strptime`` patterns in priority order.

    Returns:
        The parsed :class:`datetime.date`, or None if nothing matched.

    Defects handled: none here; the cleaner that calls it owns the tag.
    """
    token = _token(value)
    if token is None:
        return None
    for fmt in formats:
        try:
            return dt.datetime.strptime(token, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def date_format_report(
    series: pd.Series,
    formats: Sequence[str] = DATE_FORMATS,
    *,
    as_of_date: dt.date | None = None,
) -> dict[str, Any]:
    """Summarise how a string column encodes its dates.

    Args:
        series: Column of date-like strings.
        formats: Candidate ``strptime`` patterns in priority order.
        as_of_date: Reference "today". When supplied, values later than it are
            counted. WHY a parameter and not ``date.today()``: a check that
            consults the wall clock gives a different answer tomorrow, and a
            data-quality report that changes without the data changing is not
            evidence of anything.

    Returns:
        A dict with:

        * ``format_histogram``   -- {format: rows whose *first* match it was}
        * ``distinct_format_count`` -- how many encodings the column really uses
        * ``dominant_format`` / ``dominant_format_coverage`` -- the single best
          format and the fraction it covers
        * ``rows_lost_to_single_format`` -- rows a naive one-format parse would
          turn into NaT. This number is the cost of the shortcut, quantified.
        * ``unmatched_count`` / ``unmatched_examples``
        * ``ambiguous_count`` / ``ambiguous_examples`` -- values matching more
          than one format, i.e. values a parser must *guess* at
        * ``min_date`` / ``max_date`` (ISO strings, or None)
        * ``after_as_of_count`` / ``after_as_of_examples``

    Defects handled: none (observation).
    """
    histogram: dict[str, int] = {fmt: 0 for fmt in formats}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    parsed: list[dt.date] = []
    future: list[str] = []
    observable = 0
    ambiguous_count = 0

    for value in series:
        token = _token(value)
        if token is None:
            continue
        observable += 1
        matches = parseable_date_formats(token, formats)
        if not matches:
            if len(unmatched) < 5:
                unmatched.append(token)
            continue
        # WHY index 0 and not "the format that looks right": the first match is
        # the highest-priority format, which is exactly the choice a parser
        # driven by config.DATE_FORMATS would make. The histogram therefore
        # describes the parse that actually happens, not an idealised one.
        histogram[matches[0]] += 1
        if len(matches) > 1:
            ambiguous_count += 1
            if len(ambiguous) < 5:
                ambiguous.append(token)
        day = dt.datetime.strptime(token, matches[0]).date()
        parsed.append(day)
        if as_of_date is not None and day > as_of_date and len(future) < 5:
            future.append(token)

    used = {fmt: n for fmt, n in histogram.items() if n}
    dominant = max(used, key=lambda f: used[f]) if used else None
    dominant_n = used.get(dominant, 0) if dominant else 0
    after_as_of = (
        sum(1 for day in parsed if day > as_of_date) if as_of_date is not None else 0
    )

    return {
        "observable_count": observable,
        "format_histogram": histogram,
        "distinct_format_count": len(used),
        "dominant_format": dominant,
        "dominant_format_coverage": round(dominant_n / observable, 6) if observable else None,
        # WHY this key exists at all: it converts "the column has mixed formats"
        # from a qualitative remark into the number of rows a single
        # pd.to_datetime() call would silently destroy.
        "rows_lost_to_single_format": observable - dominant_n,
        "unmatched_count": observable - sum(histogram.values()),
        "unmatched_examples": unmatched,
        "ambiguous_count": ambiguous_count,
        "ambiguous_examples": ambiguous,
        "min_date": min(parsed).isoformat() if parsed else None,
        "max_date": max(parsed).isoformat() if parsed else None,
        "after_as_of_count": after_as_of,
        "after_as_of_examples": future,
    }


def identifier_match_rate(series: pd.Series) -> float:
    """Fraction of observable values shaped like a synthetic business key.

    Args:
        series: Column of strings.

    Returns:
        A value in ``[0.0, 1.0]``; 0.0 for a column with nothing observable.

    Defects handled: none (input to type inference).
    """
    tokens = [t for t in (_token(v) for v in series) if t is not None]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if _IDENTIFIER_RE.match(t))
    return hits / len(tokens)


# ── Key integrity: exact duplicates vs. genuine key collisions ───────────────
def pk_uniqueness_report(df: pd.DataFrame, key: str) -> dict[str, Any]:
    """Test a candidate primary key, splitting duplicates into two *different* findings.

    This is the most important check in the module, because the two things it
    separates look identical to ``drop_duplicates`` and are opposites in
    meaning:

    * **Exact duplicate** -- the same key appears twice and *every other column
      agrees*. The second row carries no information. Dropping it is safe and
      loses nothing.
    * **Key collision with differing payload** -- the same key appears twice and
      the rows disagree somewhere. This is **not** a duplicate. It is either a
      change captured without a version (a price that moved), a survivorship
      problem (two spellings of one store), or an upstream key bug. Whichever it
      is, ``drop_duplicates(subset=[key])`` resolves it by *file order*, which
      means the surviving value depends on how the rows happened to be shuffled.
      The finding is destroyed and the result is not even deterministic.

    Reporting them separately, with the differing columns and their conflicting
    values named, is what forces a reviewer to make an explicit decision about
    the second case instead of never learning it existed.

    Args:
        df: Frame to test.
        key: Column to treat as the business key. A missing column is reported
            as ``{"key_present": False}`` rather than raising, so this can be
            called speculatively over candidate keys.

    Returns:
        A dict with ``key_present``, ``is_unique``, ``row_count``,
        ``distinct_key_count``, ``null_key_count``, ``duplicate_key_count``
        (distinct keys appearing more than once), ``duplicated_row_count``
        (rows involved), ``exact_duplicate_keys``, ``exact_duplicate_row_count``,
        ``conflicting_keys`` and ``conflicts`` -- the last being a list of
        ``{key, row_count, differing_columns, values}`` records.

    Defects handled: none (observation). It is the evidence that distinguishes a
        safely droppable duplicate row from an undocumented change of value.
    """
    base: dict[str, Any] = {
        "key": key,
        "key_present": key in df.columns,
        "row_count": int(len(df)),
        "distinct_key_count": 0,
        "null_key_count": 0,
        "duplicate_key_count": 0,
        "duplicated_row_count": 0,
        "exact_duplicate_keys": [],
        "exact_duplicate_row_count": 0,
        "conflicting_keys": [],
        "conflicts": [],
        "is_unique": True,
    }
    if key not in df.columns or df.empty:
        # WHY not raise: the profiler probes several candidate keys and an empty
        # frame is a legitimate input (contract: never raise on the empty case).
        return base

    keys = df[key]
    null_mask = keys.map(is_missing)
    base["null_key_count"] = int(null_mask.sum())
    base["distinct_key_count"] = int(keys[~null_mask].nunique(dropna=True))

    counts = keys[~null_mask].value_counts()
    repeated = counts[counts > 1]
    base["duplicate_key_count"] = int(len(repeated))
    base["duplicated_row_count"] = int(repeated.sum())
    base["is_unique"] = bool(repeated.empty and base["null_key_count"] == 0)

    exact_keys: list[str] = []
    exact_rows = 0
    conflicts: list[dict[str, Any]] = []
    for key_value in repeated.index:
        group = df[keys == key_value]
        # WHY drop_duplicates over ALL columns: collapsing the group to its
        # distinct rows is precisely the test for "do these rows carry the same
        # information?". One survivor => byte-identical copies. More than one =>
        # the rows disagree and something real is being asserted twice.
        distinct_rows = group.drop_duplicates()
        if len(distinct_rows) == 1:
            exact_keys.append(str(key_value))
            exact_rows += int(len(group) - 1)  # surplus copies, not the original
            continue
        differing = [
            col
            for col in df.columns
            # WHY dropna=False: a NULL in one copy and a value in the other is a
            # disagreement, and the default would treat the NULL as absent and
            # under-report the conflict.
            if col != key and distinct_rows[col].nunique(dropna=False) > 1
        ]
        conflicts.append(
            {
                "key_value": str(key_value),
                "row_count": int(len(group)),
                "distinct_row_count": int(len(distinct_rows)),
                "differing_columns": differing,
                "values": {
                    col: [None if is_missing(v) else str(v) for v in distinct_rows[col].tolist()]
                    for col in differing
                },
            }
        )

    base["exact_duplicate_keys"] = exact_keys
    base["exact_duplicate_row_count"] = exact_rows
    base["conflicting_keys"] = [c["key_value"] for c in conflicts]
    base["conflicts"] = conflicts
    return base


def referential_integrity_report(
    df: pd.DataFrame,
    key: str,
    parent_keys: Iterable[Any],
    *,
    parent_name: str = "",
    max_examples: int = 25,
) -> dict[str, Any]:
    """Check that every foreign key in ``df[key]`` exists in ``parent_keys``.

    Reports both directions, because both are findings:

    * **orphans** -- child rows pointing at a parent that does not exist. These
      cannot be loaded into a star schema with foreign keys enabled, so they
      must be an explicit decision (quarantine, or an "Unknown" member) rather
      than a database error at load time.
    * **unreferenced parents** -- dimension members no fact row uses. Harmless
      for integrity, but it is how you notice a store that sold nothing, so it
      is cheap to report and occasionally the more interesting half.

    Args:
        df: The child (fact) frame.
        key: Foreign-key column in ``df``.
        parent_keys: The authoritative key set, normally taken from the
            **cleaned** dimension -- WHY cleaned: an orphan is a row that will
            have no dimension row in the warehouse, which is a stronger and more
            useful statement than "not in the raw file".
        parent_name: Label for the parent dataset, for the report only.
        max_examples: Cap on the key lists embedded in the result.

    Returns:
        A dict with ``orphan_row_count``, ``orphan_key_count``, ``orphan_keys``,
        ``orphan_rate``, ``null_key_count``, ``unreferenced_parent_count`` and
        ``unreferenced_parent_keys``.

    Defects handled: none (observation); the cleaner that acts on the orphans
        owns the tags.
    """
    parents = {str(k) for k in parent_keys}
    result: dict[str, Any] = {
        "key": key,
        "parent": parent_name,
        "key_present": key in df.columns,
        "child_row_count": int(len(df)),
        "parent_key_count": len(parents),
        "null_key_count": 0,
        "orphan_row_count": 0,
        "orphan_key_count": 0,
        "orphan_keys": [],
        "orphan_rate": 0.0,
        "unreferenced_parent_count": 0,
        "unreferenced_parent_keys": [],
    }
    if key not in df.columns or df.empty:
        result["unreferenced_parent_count"] = len(parents)
        result["unreferenced_parent_keys"] = sorted(parents)[:max_examples]
        return result

    series = df[key]
    null_mask = series.map(is_missing)
    result["null_key_count"] = int(null_mask.sum())

    present = series[~null_mask].astype(str)
    orphan_mask = ~present.isin(parents)
    orphan_values = present[orphan_mask]
    result["orphan_row_count"] = int(orphan_mask.sum())
    orphan_keys = sorted(set(orphan_values))
    result["orphan_key_count"] = len(orphan_keys)
    result["orphan_keys"] = orphan_keys[:max_examples]
    result["orphan_rate"] = (
        round(result["orphan_row_count"] / len(df), 6) if len(df) else 0.0
    )

    unreferenced = sorted(parents - set(present))
    result["unreferenced_parent_count"] = len(unreferenced)
    result["unreferenced_parent_keys"] = unreferenced[:max_examples]
    return result


# ── Arithmetic reconciliation ─────────────────────────────────────────────────
def arithmetic_reconciliation_report(
    df: pd.DataFrame,
    left: str,
    right_factors: Sequence[str],
    tolerance: float = PRICE_TOLERANCE,
    *,
    max_examples: int = 10,
) -> dict[str, Any]:
    """Test whether ``left`` equals the product of ``right_factors``, row by row.

    Sign convention, stated once so nobody has to infer it: ``delta = product -
    left``. A **positive** delta therefore means the reported figure is *lower*
    than the arithmetic implies -- money that was taken off the order.

    The reason a tolerance exists rather than ``==``: the source rounds money to
    two decimals and IEEE-754 cannot represent most cents exactly, so an exact
    comparison flags hundreds of perfectly reconciled rows. ``PRICE_TOLERANCE``
    is one cent, which is orders of magnitude below any real discount in this
    data, so the separation is unambiguous.

    Why this check matters more than it looks: when a reported total disagrees
    with quantity x price, there are two possible responses, and one of them is
    catastrophic. Recomputing the total "fixes" the rows and, in doing so,
    silently inflates revenue by the entire discount and erases the finding.
    Reporting the break -- with its row count and its dollar value -- is what
    lets someone decide that the reported total is authoritative and the
    difference is a real, quantified business fact.

    Args:
        df: Frame to test.
        left: Column holding the reported result.
        right_factors: Columns whose product should reproduce ``left``.
        tolerance: Absolute dollar tolerance. Defaults to
            :data:`src.config.PRICE_TOLERANCE`.
        max_examples: Cap on the mismatch examples embedded in the result.

    Returns:
        A dict with ``columns_present``, ``comparable_row_count``,
        ``unparseable_row_count``, ``match_count``, ``mismatch_count``,
        ``match_rate``, ``total_signed_delta``, ``total_absolute_delta``,
        ``max_absolute_delta``, ``mean_relative_delta`` and ``examples``.

    Defects handled: none (observation). It is the detector behind the
        reconciliation finding; the preserve-don't-recompute decision is the
        cleaning layer's, and carries the tag.
    """
    needed = [left, *right_factors]
    result: dict[str, Any] = {
        "left": left,
        "right_factors": list(right_factors),
        "tolerance": float(tolerance),
        "columns_present": all(col in df.columns for col in needed),
        "comparable_row_count": 0,
        "unparseable_row_count": 0,
        "match_count": 0,
        "mismatch_count": 0,
        "match_rate": None,
        "total_signed_delta": 0.0,
        "total_absolute_delta": 0.0,
        "max_absolute_delta": 0.0,
        "mean_relative_delta": None,
        "examples": [],
    }
    if not result["columns_present"] or df.empty or not right_factors:
        return result

    left_values = to_float_series(df[left])
    product = to_float_series(df[right_factors[0]])
    for factor in right_factors[1:]:
        product = product * to_float_series(df[factor])

    # WHY compute usable up front: a row where either side failed to parse is
    # neither a match nor a mismatch. Counting it as either would be a lie; the
    # honest thing is a third bucket that is reported on its own.
    usable = left_values.notna() & product.notna()
    result["unparseable_row_count"] = int((~usable).sum())
    result["comparable_row_count"] = int(usable.sum())
    if not usable.any():
        return result

    delta = (product[usable] - left_values[usable]).astype("float64")
    mismatch_mask = delta.abs() > tolerance
    result["match_count"] = int((~mismatch_mask).sum())
    result["mismatch_count"] = int(mismatch_mask.sum())
    result["match_rate"] = round(result["match_count"] / result["comparable_row_count"], 6)
    result["total_signed_delta"] = round(float(delta[mismatch_mask].sum()), 4)
    result["total_absolute_delta"] = round(float(delta[mismatch_mask].abs().sum()), 4)
    result["max_absolute_delta"] = round(float(delta.abs().max()), 4)

    if result["mismatch_count"]:
        base = product[usable][mismatch_mask].replace(0.0, float("nan")).astype("float64")
        relative = (delta[mismatch_mask] / base).abs()
        result["mean_relative_delta"] = (
            round(float(relative.mean()), 6) if relative.notna().any() else None
        )
        for idx in delta[mismatch_mask].index[:max_examples]:
            result["examples"].append(
                {
                    "row_index": int(df.index.get_loc(idx)) if idx in df.index else None,
                    "reported": round(float(left_values.loc[idx]), 4),
                    "computed": round(float(product.loc[idx]), 4),
                    "delta": round(float(delta.loc[idx]), 4),
                    "factors": {col: _token(df.at[idx, col]) for col in right_factors},
                }
            )
    return result


def value_frequency(
    series: pd.Series, limit: int = 5, *, total: int | None = None
) -> list[dict[str, Any]]:
    """Top-N value frequencies, deterministic on ties.

    Args:
        series: Column to count.
        limit: How many entries to return.
        total: Denominator for ``pct``. Defaults to the series length.

    Returns:
        ``[{"value": str|None, "count": int, "pct": float}, ...]``, sorted by
        count descending then by value ascending -- WHY the secondary sort:
        ``value_counts`` does not define tie order, so two identical runs could
        otherwise emit differently ordered JSON and pollute every diff.

    Defects handled: none (observation).
    """
    denominator = len(series) if total is None else total
    if series.empty or denominator == 0:
        return []
    counts = series.value_counts(dropna=False)
    ordered = sorted(counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    return [
        {
            "value": None if is_missing(value) else str(value),
            "count": int(count),
            "pct": round(int(count) / denominator, 6),
        }
        for value, count in ordered[:limit]
    ]


def summarize_missingness(df: pd.DataFrame) -> Mapping[str, int]:
    """Null-or-blank count per column.

    A convenience for cleaners that want the shape of the missingness without
    building a whole profile.

    Args:
        df: Frame to summarise.

    Returns:
        ``{column: count}`` in column order.

    Defects handled: none (observation).
    """
    return {str(col): int(df[col].map(is_missing).sum()) for col in df.columns}


__all__ = [
    "CURRENCY_SYMBOLS",
    "arithmetic_reconciliation_report",
    "date_format_report",
    "has_currency_marker",
    "identifier_match_rate",
    "is_blank_string",
    "is_missing",
    "is_parseable_as_decimal",
    "is_parseable_as_int",
    "parse_date_first_match",
    "parseable_date_formats",
    "pk_uniqueness_report",
    "referential_integrity_report",
    "strip_currency",
    "summarize_missingness",
    "to_float",
    "to_float_series",
    "value_frequency",
]
