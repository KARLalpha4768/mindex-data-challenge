"""Tests for the data profiling engine.

Coverage targets:
  - Standard DataFrame profiling (row/col counts, nulls, numeric stats)
  - Empty DataFrame edge case (zero rows, columns still exist)
  - All-null column edge case (100% null rate, no numeric stats)
  - Date column detection and the multi-format report (TX-01 evidence)
  - Reference-date comparison for future-dated values (TX-08 evidence)
  - None DataFrame edge case (raises TypeError)

Each test makes meaningful assertions about specific values — not just
"the function runs without error".

DOCSTRING AUDIT (finding F8, extended)
--------------------------------------
The fourth bullet above previously read "Date column detection (name-based and
content-based heuristics)" and **no such test existed** — the same
claim-without-assertion pattern the verification report caught in
``test_cleaning.py``'s PR-04 line. The profiler's date report is not incidental:
``rows_lost_to_single_format`` is the number a reviewer reads as proof that a
single ``pd.to_datetime(errors="coerce")`` call would have destroyed 20 rows,
and ``after_as_of_count`` is the raw-data evidence behind TX-08. Both are now
asserted below.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.config import AS_OF_DATE
from src.profiling.profiler import profile


def _get_col_map(result: dict) -> dict:
    """Helper to convert result['columns'] list into a name -> dict map."""
    return {c["name"]: c for c in result["columns"]}


class TestProfilerStandardCases:
    """Tests on DataFrames with representative content."""

    def test_row_and_column_counts(self) -> None:
        """Profile reports the exact shape of the input DataFrame."""
        df = pd.DataFrame({
            "id": ["1", "2", "3", "4", "5"],
            "name": ["a", "b", "c", "d", "e"],
            "score": ["10.0", "20.0", "30.0", "40.0", "50.0"],
        })
        result = profile(df, "test_standard")

        assert result["row_count"] == 5
        assert result["column_count"] == 3
        assert result["duplicate_row_count"] == 0

    def test_null_counts_and_percentages(self) -> None:
        """Nulls are counted per column with correct percentages."""
        df = pd.DataFrame({
            "a": ["1", None, "3", None, "5"],
            "b": ["x", "y", None, None, None],
        })
        result = profile(df, "test_nulls")
        col_map = _get_col_map(result)

        assert col_map["a"]["null_count"] == 2
        assert col_map["a"]["null_pct"] == 0.4
        assert col_map["b"]["null_count"] == 3
        assert col_map["b"]["null_pct"] == 0.6

    def test_duplicate_row_count(self) -> None:
        """Exact duplicate rows are counted correctly."""
        df = pd.DataFrame({
            "id": ["1", "2", "2", "3", "3", "3"],
            "val": ["a", "b", "b", "c", "c", "c"],
        })
        result = profile(df, "test_dupes")

        # 2 appears twice (1 dupe), 3 appears thrice (2 dupes) = 3 total
        assert result["duplicate_row_count"] == 3

    def test_numeric_stats_min_max_mean(self) -> None:
        """Numeric columns report min, max, mean, zero count, negative count."""
        df = pd.DataFrame({
            "val": ["0", "-5", "10", "20", "-10"],
        })
        result = profile(df, "test_numeric")
        col_map = _get_col_map(result)
        stats = col_map["val"]["numeric"]

        assert stats["min"] == -10.0
        assert stats["max"] == 20.0
        assert stats["mean"] == 3.0  # (0 + -5 + 10 + 20 + -10) / 5
        assert stats["zero_count"] == 1
        assert stats["negative_count"] == 2


class TestProfilerEdgeCases:
    """Tests on edge-case DataFrames that a naive profiler would mishandle."""

    def test_empty_dataframe(self) -> None:
        """An empty DataFrame (0 rows, N columns) is profiled without error."""
        df = pd.DataFrame(columns=["col_a", "col_b", "col_c"])
        result = profile(df, "empty_df")
        col_map = _get_col_map(result)

        assert result["row_count"] == 0
        assert result["column_count"] == 3
        assert result["duplicate_row_count"] == 0
        assert col_map["col_a"]["null_count"] == 0

    def test_all_null_column(self) -> None:
        """A column that is 100% NULL still reports correctly."""
        df = pd.DataFrame({
            "has_values": ["1", "2", "3"],
            "all_nulls": [None, None, None],
        })
        result = profile(df, "all_null_col")
        col_map = _get_col_map(result)

        assert col_map["all_nulls"]["null_count"] == 3
        assert col_map["all_nulls"]["null_pct"] == 1.0
        assert col_map["all_nulls"]["numeric"] is None

    def test_none_dataframe_raises_type_error(self) -> None:
        """Passing None as the DataFrame raises TypeError."""
        with pytest.raises(TypeError):
            profile(None, "none_df")

    def test_single_row_dataframe(self) -> None:
        """A one-row DataFrame does not produce degenerate statistics."""
        df = pd.DataFrame({"x": ["42"], "y": ["hello"]})
        result = profile(df, "single_row")
        col_map = _get_col_map(result)

        assert result["row_count"] == 1
        assert result["duplicate_row_count"] == 0
        assert col_map["x"]["null_count"] == 0


class TestProfilerDateDetection:
    """The date report — the profiler's TX-01 and TX-08 evidence."""

    def test_mixed_format_column_is_typed_as_a_date(self) -> None:
        """A column of three date encodings is classified ``date``, not free text.

        F8-class gap: the module docstring claimed date detection was covered
        and nothing tested it. Detection is content-based here — the column
        could be called anything — so a regression that fell back to
        ``free_text`` would silently suppress the whole date report, and with it
        the only pre-cleaning evidence that TX-01 exists.
        """
        df = pd.DataFrame({
            "when": ["2026-05-15", "05/16/2026", "17-05-2026", None],
        })
        col = _get_col_map(profile(df, "dates"))["when"]

        assert col["semantic_type"] == "date"
        assert col["type_confidence"] == 1.0
        assert col["date"] is not None, "a date column must carry a date report"
        assert col["numeric"] is None

    def test_report_counts_the_rows_a_single_parse_would_destroy(self) -> None:
        """TX-01 evidence: ``rows_lost_to_single_format`` is 2 of 3 observable rows.

        This number is the profiler's whole argument. One
        ``pd.to_datetime(..., format=dominant)`` pass keeps only the ISO rows and
        NaTs the rest — the previous solution's bug #2, which then
        mis-attributed the 20 lost rows to "future dates". Asserting the count
        (and the per-format histogram behind it) is what makes the argument
        checkable instead of rhetorical.
        """
        df = pd.DataFrame({
            "when": ["2026-05-15", "05/16/2026", "17-05-2026", None],
        })
        report = _get_col_map(profile(df, "dates"))["when"]["date"]

        assert report["observable_count"] == 3
        assert report["format_histogram"] == {"%Y-%m-%d": 1, "%m/%d/%Y": 1, "%d-%m-%Y": 1}
        assert report["distinct_format_count"] == 3
        assert report["rows_lost_to_single_format"] == 2
        assert report["unmatched_count"] == 0
        assert (report["min_date"], report["max_date"]) == ("2026-05-15", "2026-05-17")

    def test_single_format_column_loses_nothing(self) -> None:
        """A clean ISO column reports one format and zero rows at risk.

        The negative case. Without it, ``rows_lost_to_single_format`` could be
        hardcoded to "everything but the first format" and still pass the test
        above.
        """
        df = pd.DataFrame({"when": ["2026-05-15", "2026-05-16", "2026-05-17"]})
        report = _get_col_map(profile(df, "iso_dates"))["when"]["date"]

        assert report["distinct_format_count"] == 1
        assert report["dominant_format"] == "%Y-%m-%d"
        assert report["dominant_format_coverage"] == 1.0
        assert report["rows_lost_to_single_format"] == 0

    def test_future_dates_are_counted_against_the_reference_date(self) -> None:
        """TX-08 evidence: dates past AS_OF_DATE are counted before any cleaning.

        AS_OF_DATE is 2026-06-02, so 2026-07-01 is future-dated and 2026-06-02
        itself is not — the comparison is inclusive of the reference day. This
        is the profiler's independent corroboration of the three TX-08 rows the
        cleaner quarantines, and nothing asserted it.
        """
        assert AS_OF_DATE.isoformat() == "2026-06-02", "fixture guard"
        df = pd.DataFrame({
            "when": ["2026-05-15", "2026-06-02", "2026-07-01", "2026-08-20"],
        })
        report = _get_col_map(profile(df, "future_dates"))["when"]["date"]

        assert report["after_as_of_count"] == 2
        assert sorted(report["after_as_of_examples"]) == ["2026-07-01", "2026-08-20"]
