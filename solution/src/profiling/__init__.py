"""Profiling layer: measures the raw files *before* any cleaning touches them.

Two modules, one rule between them: **observe, never decide.**

* :mod:`src.profiling.checks` -- composable, dataset-agnostic check primitives
  (parseability, date-format detection, key uniqueness, referential integrity,
  arithmetic reconciliation). The cleaning layer reuses these, so profiling and
  cleaning agree by construction on what a date, a number and a duplicate are.
* :mod:`src.profiling.profiler` -- ``profile(df, name)``, a generic profiler
  that runs unchanged over every dataset and emits the evidence written to
  ``output/profile_report.json``.

Neither module imports the defect catalog. The profile is meant to let a
reviewer notice the problems independently; a profiler that was handed the
answer key would prove nothing.

Defect codes owned: none. The profile is the "before" photograph that makes the
cleaning layer's later claims checkable.
"""

from src.profiling.checks import (
    arithmetic_reconciliation_report,
    date_format_report,
    has_currency_marker,
    is_blank_string,
    is_missing,
    is_parseable_as_decimal,
    is_parseable_as_int,
    parse_date_first_match,
    parseable_date_formats,
    pk_uniqueness_report,
    referential_integrity_report,
    strip_currency,
    to_float,
    to_float_series,
    value_frequency,
)
from src.profiling.profiler import (
    SEMANTIC_TYPES,
    discover_numeric_relationships,
    infer_semantic_type,
    profile,
    profile_column,
    profile_datasets,
)

__all__ = [
    "SEMANTIC_TYPES",
    "arithmetic_reconciliation_report",
    "date_format_report",
    "discover_numeric_relationships",
    "has_currency_marker",
    "infer_semantic_type",
    "is_blank_string",
    "is_missing",
    "is_parseable_as_decimal",
    "is_parseable_as_int",
    "parse_date_first_match",
    "parseable_date_formats",
    "pk_uniqueness_report",
    "profile",
    "profile_column",
    "profile_datasets",
    "referential_integrity_report",
    "strip_currency",
    "to_float",
    "to_float_series",
    "value_frequency",
]
