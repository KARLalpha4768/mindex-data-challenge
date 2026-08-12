"""Generic, reusable data profiler: measures a raw dataset before anything edits it.

What this module is for
-----------------------
``profile(df, name)`` takes any string-typed DataFrame -- as produced by
:func:`src.io_utils.read_csv_as_str` -- and returns a dict describing it. It runs
**unchanged** over stores, products and transactions, and would run unchanged
over a fourth file nobody has written yet. That is not a stylistic preference:
a profiler with per-dataset branches is a report generator, and a report
generator tells you only what its author already suspected.

The design rule, stated once
----------------------------
**The profiler observes. The cleaner decides.**

Nothing here imports :data:`src.defects.DEFECT_CATALOG`, and no check anywhere in
this file mentions a store ID, a column name or a defect code. Every number it
emits is a general-purpose measurement. The measurements happen to make the
seeded defects obvious to anyone reading ``output/profile_report.json`` --
which is the whole point. A profile that finds problems *because it was told
where they are* proves nothing; a profile that surfaces them because it measures
the right generic things is evidence.

Concretely, a reviewer reading the output can independently notice:

* columns whose null rate is non-zero, and how big it is;
* a candidate key that repeats, split into *byte-identical copies* versus
  *collisions whose payloads disagree* -- with the disagreeing columns named;
* a date column that uses more than one encoding, and how many rows a single
  ``pd.to_datetime`` call would destroy;
* a numeric column written as currency text, with the marker count;
* dates later than the configured reference date;
* zero and negative counts per numeric column;
* character-length spread per column (a five-digit code that is four characters
  long shows up here and nowhere else);
* multiplicative relationships between numeric columns that hold for most rows
  but not all -- discovered by search, not by being told which columns to test.

Type inference
--------------
Every column arrives as ``object`` holding ``str``, so the pandas dtype conveys
nothing. The interesting work is inferring a *semantic* type -- integer,
decimal, currency string, date, identifier, categorical, free text -- from the
values themselves, and reporting the evidence and the confidence alongside, so
the inference can be disagreed with rather than trusted.

Defect codes owned: **none, by design.** This module detects nothing and
decides nothing; it measures. Consequently it carries no ``# DEFECT:`` tags --
those belong on the lines in ``src/cleaning/*`` that take action.

Inputs:  a string-typed :class:`pandas.DataFrame` and its dataset name.
Outputs: a JSON-safe dict (see :func:`profile` for the exact schema), which
         ``src/pipeline.py`` writes to ``output/profile_report.json``.
"""

from __future__ import annotations

import datetime as dt
from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from src.config import AS_OF_DATE, DATE_FORMATS, PRICE_TOLERANCE
from src.io_utils import json_safe
from src.profiling.checks import (
    arithmetic_reconciliation_report,
    date_format_report,
    has_currency_marker,
    identifier_match_rate,
    is_blank_string,
    is_missing,
    is_parseable_as_decimal,
    is_parseable_as_int,
    parseable_date_formats,
    pk_uniqueness_report,
    referential_integrity_report,
    to_float,
    to_float_series,
    value_frequency,
)

# ── Semantic type vocabulary ──────────────────────────────────────────────────
# WHY a closed, named vocabulary: these strings are consumed by the cleaning
# layer, the tests and the dashboard. An ad-hoc string invented at one call site
# is a bug nobody sees until a dashboard filter silently matches nothing.
SEMANTIC_TYPES: tuple[str, ...] = (
    "empty",  # nothing observable at all -- neither a number nor a string worth typing
    "integer",
    "decimal",
    "currency_string",
    "date",
    "identifier",
    "categorical",
    "free_text",
)

# ── Inference thresholds ──────────────────────────────────────────────────────
# WHY thresholds rather than "all values must match": real columns have a few
# bad rows, and a rule that demands perfection degrades every partially-broken
# column to "free_text" -- which is the one label that carries no information
# and triggers no further statistics. The confidence is reported alongside the
# type, so a 0.96 call is visibly weaker than a 1.00 one.
TYPE_CONFIDENCE_THRESHOLD: float = 0.95
"""Fraction of observable values that must satisfy a grammar to claim its type."""

CATEGORICAL_MAX_DISTINCT: int = 25
"""Above this many distinct values a column is not a category, whatever its
cardinality ratio says."""

CATEGORICAL_MAX_DISTINCT_RATIO: float = 0.5
"""And it must also repeat: distinct/observable at or below this."""

IDENTIFIER_MIN_DISTINCT_RATIO: float = 0.5
"""Identifier-shaped values below this repetition level are better described as
a category (a 5-value ``supplier_id`` is more usefully profiled with a frequency
table than as a key)."""

TOP_VALUES_LIMIT: int = 5
"""How many entries in the frequency table (contract: top-5)."""

SAMPLE_VALUE_COUNT: int = 5
"""How many sample values per column (contract: 5, deterministic)."""

LENGTH_HISTOGRAM_LIMIT: int = 10
"""Cap on distinct character lengths reported per column."""

PARSE_FAILURE_EXAMPLE_LIMIT: int = 5

MAX_RELATIONSHIP_COLUMNS: int = 8
"""Skip relationship discovery above this many numeric columns. WHY a cap: the
search is over ordered (left, factor-pair) triples, which grows as n^3. Eight
columns is 168 tests -- trivial -- while a 60-column extract would be 200k and
would turn a profiler into a batch job."""

RELATIONSHIP_MIN_MATCH_RATE: float = 0.5
"""A discovered relationship is only reported if it holds for at least this
fraction of comparable rows. WHY: coincidental products exist, but they do not
hold for half a file. A rate of 1.0 means a clean derived column; a rate just
under 1.0 is the interesting case -- a rule with exceptions."""


# ── Small helpers ─────────────────────────────────────────────────────────────
def _human_bytes(size: int) -> str:
    """Render a byte count as a short human string.

    Args:
        size: Number of bytes.

    Returns:
        e.g. ``"1.2 MB"``. Included next to the raw number, never instead of it.

    Defects handled: none (presentation helper).
    """
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} GB"  # pragma: no cover - unreachable, kept for total-ness


def _ratio(numerator: int, denominator: int) -> float:
    """Divide, returning 0.0 when the denominator is zero.

    WHY a helper rather than inline guards: every percentage in this module has
    a zero-row edge case (empty DataFrame), and one forgotten guard is a
    ``ZeroDivisionError`` in the middle of a profiling run.

    Args:
        numerator: Top of the fraction.
        denominator: Bottom of the fraction.

    Returns:
        The ratio rounded to 6dp, or 0.0.

    Defects handled: none (helper).
    """
    if not denominator:
        return 0.0
    return round(numerator / denominator, 6)


def _sample_positions(n: int, k: int) -> list[int]:
    """Pick up to ``k`` evenly spaced row positions from ``n`` rows.

    WHY evenly spaced rather than ``head(5)`` or a random sample: the head of a
    file is often unrepresentative (sorted data, header-adjacent oddities), and
    a random sample makes the report non-reproducible, so two runs over the same
    bytes would produce a diff. Even spacing is deterministic *and* shows the
    reader values from across the file.

    Args:
        n: Number of rows available.
        k: Maximum number of positions wanted.

    Returns:
        Ascending, de-duplicated positions.

    Defects handled: none (helper).
    """
    if n <= 0 or k <= 0:
        return []
    if n <= k:
        return list(range(n))
    step = (n - 1) / (k - 1) if k > 1 else 0
    positions = sorted({int(round(i * step)) for i in range(k)})
    return [p for p in positions if 0 <= p < n]


def _length_histogram(tokens: Sequence[str]) -> dict[str, int]:
    """Character-length distribution of the observable values.

    Small, generic, and disproportionately useful: a fixed-width code that is
    one character short shows up here as a second bucket, which no null count,
    distinct count or numeric range would ever reveal.

    Args:
        tokens: Trimmed, non-missing string values.

    Returns:
        ``{"5": 14, "4": 1}`` -- string keys because JSON object keys are
        strings anyway, and an int-keyed dict would silently change shape on the
        round trip.

    Defects handled: none (observation).
    """
    counts: dict[int, int] = {}
    for token in tokens:
        counts[len(token)] = counts.get(len(token), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {str(length): count for length, count in ordered[:LENGTH_HISTOGRAM_LIMIT]}


# ── Type inference ────────────────────────────────────────────────────────────
def _type_evidence(series: pd.Series, tokens: Sequence[str]) -> dict[str, Any]:
    """Measure every grammar a column might satisfy, before choosing one.

    Args:
        series: The raw column (used for the date report, which needs the whole
            series to build its histogram).
        tokens: The trimmed, non-missing values.

    Returns:
        A dict of rates in ``[0, 1]`` plus the raw counts behind them. This is
        embedded verbatim in the profile so the inference is *auditable*: a
        reader who disagrees with the label can see exactly which rates produced
        it.

    Defects handled: none (observation).
    """
    n = len(tokens)
    if not n:
        return {
            "observable_count": 0,
            "int_rate": 0.0,
            "decimal_rate": 0.0,
            "currency_rate": 0.0,
            "currency_marker_count": 0,
            "date_rate": 0.0,
            "identifier_rate": 0.0,
            "distinct_ratio": 0.0,
        }

    int_hits = sum(1 for t in tokens if is_parseable_as_int(t))
    decimal_hits = sum(1 for t in tokens if is_parseable_as_decimal(t))
    marker_hits = sum(1 for t in tokens if has_currency_marker(t))
    # WHY "decimal after stripping currency" is its own rate: a column of 480
    # bare floats and 25 "$142.50" strings has a decimal_rate of 0.95 but a
    # currency_rate of 1.00. Only the second one is the truth about the column,
    # and the gap between them is the size of the problem.
    currency_hits = sum(1 for t in tokens if to_float(t) is not None)
    date_hits = sum(1 for t in tokens if parseable_date_formats(t, DATE_FORMATS))

    return {
        "observable_count": n,
        "int_rate": round(int_hits / n, 6),
        "decimal_rate": round(decimal_hits / n, 6),
        "currency_rate": round(currency_hits / n, 6),
        "currency_marker_count": marker_hits,
        "date_rate": round(date_hits / n, 6),
        "identifier_rate": round(identifier_match_rate(series), 6),
        "distinct_ratio": round(len(set(tokens)) / n, 6),
    }


def infer_semantic_type(
    evidence: Mapping[str, Any], *, column_name: str = ""
) -> tuple[str, float]:
    """Choose a semantic type from the measured evidence.

    Precedence is deliberate and each step is here for a reason:

    1. **empty** -- nothing observable. Any other label would be a guess
       dressed up as a measurement.
    2. **date** before numeric. WHY: nothing that parses as a date under an
       explicit ``strptime`` grammar is better described as a number, and
       ``%Y-%m-%d`` cannot collide with an integer or decimal literal anyway.
    3. **currency_string** before decimal. WHY: it is only reachable when the
       column *contains* currency markers, and that fact -- "this money column
       is text" -- is the finding. Calling it "decimal" because 95% of the rows
       happen to be bare numbers buries it.
    4. **integer** before decimal, since integers are a strict subset.
    5. **identifier** before categorical only when the values actually repeat
       rarely; a low-cardinality ID-shaped column is more usefully profiled as a
       category with a frequency table.
    6. **categorical**, then **free_text** as the residual.

    Args:
        evidence: Output of :func:`_type_evidence`.
        column_name: Used only as a weak tie-break for identifier detection
            (a ``*_id``/``*_key`` suffix). WHY only weak: names lie, values do
            not, so the name may promote an already ID-shaped column but can
            never override the value evidence.

    Returns:
        ``(semantic_type, confidence)`` where confidence is the fraction of
        observable values satisfying the chosen grammar. Types that impose no
        grammar (identifier, categorical, free_text) report 1.0.

    Defects handled: none (observation).
    """
    n = int(evidence.get("observable_count", 0))
    if not n:
        return "empty", 1.0

    threshold = TYPE_CONFIDENCE_THRESHOLD
    date_rate = float(evidence["date_rate"])
    int_rate = float(evidence["int_rate"])
    decimal_rate = float(evidence["decimal_rate"])
    currency_rate = float(evidence["currency_rate"])
    markers = int(evidence["currency_marker_count"])
    identifier_rate = float(evidence["identifier_rate"])
    distinct_ratio = float(evidence["distinct_ratio"])

    if date_rate >= threshold:
        return "date", date_rate
    if markers > 0 and currency_rate >= threshold:
        return "currency_string", currency_rate
    if int_rate >= threshold:
        return "integer", int_rate
    if decimal_rate >= threshold:
        return "decimal", decimal_rate

    name_suggests_key = column_name.lower().endswith(("_id", "_key", "id", "code"))
    looks_like_key = identifier_rate >= threshold or (
        name_suggests_key and identifier_rate >= 0.5
    )
    if looks_like_key and distinct_ratio >= IDENTIFIER_MIN_DISTINCT_RATIO:
        return "identifier", identifier_rate

    distinct_count = round(distinct_ratio * n)
    if distinct_count <= CATEGORICAL_MAX_DISTINCT and distinct_ratio <= CATEGORICAL_MAX_DISTINCT_RATIO:
        return "categorical", 1.0
    if looks_like_key:
        # WHY this second chance: an ID-shaped column that repeats a lot failed
        # the ratio gate above but is still better described as an identifier
        # than as free text once it is also too high-cardinality to be a category.
        return "identifier", identifier_rate
    return "free_text", 1.0


def _parse_failures(
    tokens: Sequence[str], semantic_type: str
) -> tuple[int, list[str]]:
    """Count observable values that do **not** satisfy the inferred type.

    This is the number that keeps the inference honest. A column labelled
    ``date`` with 20 parse failures is not a date column that "mostly works" --
    it is 20 rows that a naive parser would turn into NaT and a naive
    ``dropna()`` would delete without telling anyone. Publishing the count next
    to the type means the reader sees the exception rate, not just the label.

    Types that impose no grammar (identifier, categorical, free_text, empty)
    report zero, because there is nothing they could fail to parse as.

    Args:
        tokens: Trimmed, non-missing values.
        semantic_type: The inferred type.

    Returns:
        ``(count, examples)`` with examples capped for readability.

    Defects handled: none (observation).
    """
    predicates = {
        "integer": is_parseable_as_int,
        "decimal": is_parseable_as_decimal,
        "currency_string": lambda v: to_float(v) is not None,
        "date": lambda v: bool(parseable_date_formats(v, DATE_FORMATS)),
    }
    predicate = predicates.get(semantic_type)
    if predicate is None:
        return 0, []
    failures = [t for t in tokens if not predicate(t)]
    return len(failures), failures[:PARSE_FAILURE_EXAMPLE_LIMIT]


# ── Per-column statistics ─────────────────────────────────────────────────────
def _numeric_stats(series: pd.Series) -> dict[str, Any]:
    """Descriptive statistics for a numeric-inferrable column.

    Args:
        series: The raw string column; parsed here via
            :func:`src.profiling.checks.to_float_series`, so currency-formatted
            values are included rather than silently dropped.

    Returns:
        ``min``/``max``/``mean``/``sum`` plus ``zero_count`` and
        ``negative_count``.

        WHY zero and negative counts are first-class rather than left to the
        min: a price of ``0.00`` and a quantity of ``0`` are both perfectly
        ordinary-looking minima, and both are usually wrong; negative values are
        how a returns population announces itself. All three are invisible in a
        mean and easy to miss in a min. Counting them costs one pass and turns
        "the minimum is 0" into "5 rows are exactly 0".

    Defects handled: none (observation).
    """
    values = to_float_series(series).dropna()
    if values.empty:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "sum": None,
            "zero_count": 0,
            "negative_count": 0,
            "parsed_count": 0,
        }
    return {
        "min": round(float(values.min()), 6),
        "max": round(float(values.max()), 6),
        "mean": round(float(values.mean()), 6),
        "sum": round(float(values.sum()), 6),
        "zero_count": int((values == 0).sum()),
        "negative_count": int((values < 0).sum()),
        "parsed_count": int(len(values)),
    }


def profile_column(
    series: pd.Series,
    *,
    name: str,
    position: int,
    row_count: int,
    as_of_date: dt.date,
) -> dict[str, Any]:
    """Profile a single column.

    Args:
        series: The column, as strings.
        name: Column name.
        position: Zero-based ordinal in the frame, so a reader can line the
            report up against the CSV header.
        row_count: Row count of the parent frame -- passed in rather than taken
            from ``len(series)`` so every percentage in the report shares one
            denominator.
        as_of_date: Reference "today" for date columns.

    Returns:
        The column block described in :func:`profile`.

    Defects handled: none (observation).
    """
    # ── Missingness ──────────────────────────────────────────────────────────
    # WHY blanks are counted separately from nulls: read_csv_as_str turns empty
    # fields into NaN by default, but a value of "   " survives as a present
    # string. Reporting one number for both would let whitespace-only data hide
    # inside a "0 nulls" column.
    missing_mask = series.map(is_missing)
    null_count = int(missing_mask.sum())
    blank_count = int(series.map(is_blank_string).sum())

    tokens: list[str] = [str(v).strip() for v in series[~missing_mask]]

    distinct_count = int(series[~missing_mask].nunique(dropna=True))

    evidence = _type_evidence(series, tokens)
    semantic_type, confidence = infer_semantic_type(evidence, column_name=name)
    parse_failure_count, parse_failure_examples = _parse_failures(tokens, semantic_type)

    # ── Sampling ─────────────────────────────────────────────────────────────
    sample_values = [
        None if is_missing(series.iloc[p]) else str(series.iloc[p])
        for p in _sample_positions(len(series), SAMPLE_VALUE_COUNT)
    ]

    # ── Frequencies ──────────────────────────────────────────────────────────
    # WHY only for low cardinality: a top-5 table over 490 distinct transaction
    # IDs is five rows of noise, whereas over a 4-value region column it is the
    # entire distribution -- including the fact that two rows have none.
    top_values = (
        value_frequency(series, TOP_VALUES_LIMIT, total=row_count)
        if 0 < distinct_count <= CATEGORICAL_MAX_DISTINCT
        else []
    )

    block: dict[str, Any] = {
        "name": name,
        "position": position,
        "dtype": str(series.dtype),
        "semantic_type": semantic_type,
        "type_confidence": round(float(confidence), 6),
        "non_null_count": row_count - null_count,
        "null_count": null_count,
        "null_pct": _ratio(null_count, row_count),
        "blank_count": blank_count,
        "distinct_count": distinct_count,
        "distinct_pct": _ratio(distinct_count, row_count),
        "is_unique": bool(distinct_count == row_count and null_count == 0 and row_count > 0),
        "is_constant": bool(distinct_count == 1),
        "min_length": min((len(t) for t in tokens), default=None),
        "max_length": max((len(t) for t in tokens), default=None),
        "length_histogram": _length_histogram(tokens),
        "top_values": top_values,
        "sample_values": sample_values,
        "parse_failures": parse_failure_count,
        "parse_failure_examples": parse_failure_examples,
        "type_evidence": evidence,
        # Filled in below only when the inferred type warrants it; always
        # present as a key so consumers never branch on key existence.
        "numeric": None,
        "date": None,
    }

    if semantic_type in ("integer", "decimal", "currency_string"):
        stats = _numeric_stats(series)
        stats["currency_marker_count"] = int(evidence["currency_marker_count"])
        block["numeric"] = stats
    elif semantic_type == "date":
        block["date"] = date_format_report(series, DATE_FORMATS, as_of_date=as_of_date)

    return block


# ── Cross-column discovery ────────────────────────────────────────────────────
def discover_numeric_relationships(
    df: pd.DataFrame,
    numeric_columns: Sequence[str],
    *,
    tolerance: float = PRICE_TOLERANCE,
) -> list[dict[str, Any]]:
    """Search for ``left ~= product(right_factors)`` relationships among columns.

    This is the generic version of a reconciliation check. Rather than being
    told which columns should multiply out, it tries every ordered
    (target, factor-pair) combination and reports those that hold for most rows.

    WHY this belongs in a *profiler*: a derived column that agrees with its
    inputs on 95% of rows is one of the highest-value findings available in any
    dataset, and it is completely invisible to per-column statistics -- the
    three columns individually look perfectly healthy. Discovering the rule by
    search rather than by assertion also means the exceptions are found the same
    way a reviewer would find them, which is what makes the finding credible
    rather than pre-arranged.

    The report deliberately stops at "this rule holds for N rows and fails for
    M, worth $X". It offers no opinion on which side is correct. That decision
    -- and it is a consequential one, since "correcting" the failures by
    recomputing the target would change the totals the business reports -- is
    the cleaning layer's to make and to justify.

    Args:
        df: The frame.
        numeric_columns: Columns inferred as integer/decimal/currency_string.
        tolerance: Absolute tolerance passed through to
            :func:`~src.profiling.checks.arithmetic_reconciliation_report`.

    Returns:
        A list of reconciliation reports whose match rate clears
        :data:`RELATIONSHIP_MIN_MATCH_RATE`, best rate first. Empty when there
        are fewer than three numeric columns or too many to search cheaply.

    Defects handled: none (observation).
    """
    columns = list(numeric_columns)
    if len(columns) < 3 or len(columns) > MAX_RELATIONSHIP_COLUMNS or df.empty:
        return []

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for left in columns:
        others = [c for c in columns if c != left]
        for pair in combinations(others, 2):
            # WHY sorted factors and a seen-set: multiplication commutes, so
            # (qty, price) and (price, qty) are the same hypothesis. Testing
            # both would double the work and emit a duplicate finding.
            signature = (left, tuple(sorted(pair)))
            if signature in seen:
                continue
            seen.add(signature)
            report = arithmetic_reconciliation_report(
                df, left, list(signature[1]), tolerance
            )
            rate = report.get("match_rate")
            if rate is not None and rate >= RELATIONSHIP_MIN_MATCH_RATE:
                report["relationship"] = f"{left} ~= {' * '.join(signature[1])}"
                found.append(report)
    found.sort(key=lambda r: (-(r.get("match_rate") or 0.0), r["left"]))
    return found


def _candidate_key_columns(columns: Sequence[Mapping[str, Any]]) -> list[str]:
    """Pick the columns worth testing as a primary key.

    Args:
        columns: The per-column blocks already produced by
            :func:`profile_column`.

    Returns:
        Column names inferred as identifiers or observed to be unique, ordered
        by distinctness descending. WHY both criteria: a genuinely unique column
        is a candidate key whatever it looks like, and an identifier-shaped
        column is a candidate key *especially* when it is not unique -- that is
        the case worth reporting.

    Defects handled: none (observation).
    """
    candidates = [
        block
        for block in columns
        if block["semantic_type"] == "identifier" or block["is_unique"]
    ]
    candidates.sort(key=lambda b: (-float(b["distinct_pct"]), int(b["position"])))
    return [str(b["name"]) for b in candidates]


# ── Public entry point ────────────────────────────────────────────────────────
def profile(
    df: pd.DataFrame,
    name: str,
    *,
    as_of_date: dt.date = AS_OF_DATE,
    reference_keys: Mapping[str, Iterable[Any]] | None = None,
) -> dict[str, Any]:
    """Profile one dataset.

    Runs unchanged over any string-typed frame. The output is JSON-safe: it is
    passed through :func:`src.io_utils.json_safe` on the way out, so no numpy
    scalar, NaN, NaT or Timestamp can reach ``json.dump`` and abort a run at the
    very last step.

    Output schema::

        {
          "dataset": str,
          "row_count": int,
          "column_count": int,
          "duplicate_row_count": int,          # full-row duplicates, surplus copies
          "duplicate_row_pct": float,
          "memory_bytes": int,
          "memory_human": str,
          "as_of_date": "YYYY-MM-DD",
          "columns": [ {
              "name": str, "position": int, "dtype": str,
              "semantic_type": str,            # one of SEMANTIC_TYPES
              "type_confidence": float,
              "non_null_count": int, "null_count": int, "null_pct": float,
              "blank_count": int,
              "distinct_count": int, "distinct_pct": float,
              "is_unique": bool, "is_constant": bool,
              "min_length": int|None, "max_length": int|None,
              "length_histogram": {str: int},
              "top_values": [ {"value": str|None, "count": int, "pct": float} ],
              "sample_values": [str|None],     # 5, evenly spaced, deterministic
              "parse_failures": int, "parse_failure_examples": [str],
              "type_evidence": { ...rates... },
              "numeric": None | {"min","max","mean","sum","zero_count",
                                 "negative_count","parsed_count",
                                 "currency_marker_count"},
              "date":    None | {"observable_count","format_histogram",
                                 "distinct_format_count","dominant_format",
                                 "dominant_format_coverage",
                                 "rows_lost_to_single_format",
                                 "unmatched_count","unmatched_examples",
                                 "ambiguous_count","ambiguous_examples",
                                 "min_date","max_date",
                                 "after_as_of_count","after_as_of_examples"}
          } ],
          "candidate_keys": [ pk_uniqueness_report(...) ],
          "numeric_relationships": [ arithmetic_reconciliation_report(...) ],
          "referential_integrity": [ referential_integrity_report(...) ]
        }

    Args:
        df: The dataset, every column as strings.
        name: Dataset name, echoed into the report.
        as_of_date: Reference "today" for the date statistics. Defaults to
            :data:`src.config.AS_OF_DATE`; never the wall clock, so the same
            input always produces the same report.
        reference_keys: Optional ``{column: allowed key values}``. When supplied
            the profile gains a referential-integrity block per entry. Left
            empty by the pipeline, because at profiling time the authoritative
            key sets do not exist yet -- the cleaned dimensions define them, and
            they are checked in the cleaning layer where the decision about
            orphans is made and audited. The parameter exists so the profiler
            stays useful outside this pipeline.

    Returns:
        The dict above, fully JSON-safe.

    Raises:
        TypeError: If ``df`` is not a DataFrame. Every *data* edge case --
            zero rows, zero columns, an all-null column, a constant column, a
            column of empty strings -- is handled and returns a well-formed
            report. Only a wrong argument type is an error.

    Defects handled: none, deliberately. This function measures; the cleaning
        modules decide and carry the ``# DEFECT:`` tags.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"profile() expects a DataFrame for {name!r}, got {type(df).__name__}")

    row_count = int(len(df))
    column_count = int(len(df.columns))

    # ── Dataset-level shape ──────────────────────────────────────────────────
    # WHY duplicated() with the default keep="first": the count answers "how
    # many rows could be removed without losing information", which is the
    # question a reviewer is actually asking. keep=False would count the
    # originals too and roughly double the number for no added meaning.
    # An empty frame short-circuits: DataFrame.duplicated() on zero columns is
    # legal but degenerate, and returning 0 is both correct and safe.
    duplicate_row_count = int(df.duplicated().sum()) if row_count and column_count else 0

    columns: list[dict[str, Any]] = [
        profile_column(
            df[column],
            name=str(column),
            position=index,
            row_count=row_count,
            as_of_date=as_of_date,
        )
        for index, column in enumerate(df.columns)
    ]

    # ── Candidate-key integrity ──────────────────────────────────────────────
    # This is where a repeated business key is split into "identical copies" and
    # "same key, different payload" -- two findings that a single duplicate
    # count, and every drop_duplicates() call, conflate into one.
    candidate_keys = [
        pk_uniqueness_report(df, key) for key in _candidate_key_columns(columns)
    ]

    # ── Cross-column arithmetic ──────────────────────────────────────────────
    numeric_columns = [
        block["name"]
        for block in columns
        if block["semantic_type"] in ("integer", "decimal", "currency_string")
    ]
    relationships = discover_numeric_relationships(df, numeric_columns)

    # ── Optional referential integrity ───────────────────────────────────────
    integrity: list[dict[str, Any]] = []
    if reference_keys:
        for column, allowed in reference_keys.items():
            integrity.append(
                referential_integrity_report(df, column, allowed, parent_name=str(column))
            )

    report: dict[str, Any] = {
        "dataset": str(name),
        "row_count": row_count,
        "column_count": column_count,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_row_pct": _ratio(duplicate_row_count, row_count),
        "memory_bytes": int(df.memory_usage(deep=True).sum()) if column_count else 0,
        "memory_human": _human_bytes(
            int(df.memory_usage(deep=True).sum()) if column_count else 0
        ),
        "as_of_date": as_of_date.isoformat(),
        "columns": columns,
        "candidate_keys": candidate_keys,
        "numeric_relationships": relationships,
        "referential_integrity": integrity,
    }

    # WHY the json_safe round trip rather than trusting the int()/float() casts
    # above: pandas aggregates leak numpy scalars from places that are easy to
    # miss, and the failure mode is a TypeError at the end of the pipeline with
    # no indication of which of several thousand values caused it. One cheap
    # normalisation here makes the contract ("this dict is JSON-serialisable")
    # true by construction rather than by vigilance.
    return json_safe(report)


def profile_datasets(
    frames: Mapping[str, pd.DataFrame], *, as_of_date: dt.date = AS_OF_DATE
) -> dict[str, Any]:
    """Profile several datasets at once.

    Args:
        frames: ``{dataset_name: dataframe}``.
        as_of_date: Reference "today".

    Returns:
        ``{dataset_name: profile}`` in the iteration order of ``frames``.

    Defects handled: none (observation).
    """
    return {name: profile(frame, name, as_of_date=as_of_date) for name, frame in frames.items()}


__all__ = [
    "CATEGORICAL_MAX_DISTINCT",
    "SEMANTIC_TYPES",
    "TYPE_CONFIDENCE_THRESHOLD",
    "discover_numeric_relationships",
    "infer_semantic_type",
    "profile",
    "profile_column",
    "profile_datasets",
]
