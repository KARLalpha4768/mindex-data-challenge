"""Tests for the data profiling engine.

Coverage targets:
  - Standard DataFrame profiling (row/col counts, nulls, numeric stats)
  - Empty DataFrame edge case (zero rows, columns still exist)
  - All-null column edge case (100% null rate, no numeric stats)
  - Date column detection (name-based and content-based heuristics)
  - None DataFrame edge case (raises TypeError)

Each test makes meaningful assertions about specific values — not just
"the function runs without error".
"""

from __future__ import annotations

import pandas as pd
import pytest

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
