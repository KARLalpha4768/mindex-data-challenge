"""
Data Profiling Module for Mindex Data Pipeline.
Generates comprehensive quality metadata for input pandas DataFrames.
"""
from datetime import datetime
import json
import os
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd


def is_date_column(col_name: str, series: pd.Series) -> bool:
    """Check if a series appears to represent date/time data."""
    if "date" in col_name.lower() or "time" in col_name.lower() or "timestamp" in col_name.lower():
        return True
    if series.dtype == "object":
        sample = series.dropna().head(100)
        if sample.empty:
            return False
        parsed = pd.to_datetime(sample, errors="coerce")
        if parsed.notnull().mean() > 0.8:
            return True
    return pd.api.types.is_datetime64_any_dtype(series)


def profile(df: pd.DataFrame, name: str) -> Dict[str, Any]:
    """
    Profile a given DataFrame and return a structured quality summary.
    
    Args:
        df: Input pandas DataFrame to profile.
        name: Identifier name for the dataset.
        
    Returns:
        Dict containing quality metrics (row/col count, nulls, duplicates, numeric/date stats).
    """
    if df is None:
        return {"dataset_name": name, "error": "DataFrame is None"}
        
    row_count, col_count = df.shape
    duplicate_rows = int(df.duplicated().sum())
    
    column_stats: Dict[str, Any] = {}
    today_dt = pd.Timestamp.now().normalize()
    
    for col in df.columns:
        series = df[col]
        null_count = int(series.isnull().sum())
        null_pct = round((null_count / row_count * 100), 2) if row_count > 0 else 0.0
        
        c_info: Dict[str, Any] = {
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_percentage": null_pct,
            "unique_values": int(series.nunique(dropna=True))
        }
        
        # Numeric column metrics
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            valid_num = series.dropna()
            if not valid_num.empty:
                c_info["numeric_stats"] = {
                    "min": float(valid_num.min()),
                    "max": float(valid_num.max()),
                    "mean": round(float(valid_num.mean()), 4),
                    "count_zeros": int((valid_num == 0).sum()),
                    "count_negatives": int((valid_num < 0).sum())
                }
            else:
                c_info["numeric_stats"] = None
                
        # Date column metrics
        if is_date_column(col, series):
            dt_series = pd.to_datetime(series, errors="coerce")
            valid_dt = dt_series.dropna()
            if not valid_dt.empty:
                future_count = int((valid_dt > today_dt).sum())
                c_info["date_stats"] = {
                    "min_date": valid_dt.min().strftime("%Y-%m-%d %H:%M:%S"),
                    "max_date": valid_dt.max().strftime("%Y-%m-%d %H:%M:%S"),
                    "future_dates_count": future_count
                }
            else:
                c_info["date_stats"] = None
                
        column_stats[col] = c_info
        
    return {
        "dataset_name": name,
        "profiled_at": datetime.now().isoformat(),
        "summary": {
            "row_count": row_count,
            "column_count": col_count,
            "duplicate_rows": duplicate_rows
        },
        "columns": column_stats
    }


def profile_raw_data(data_dir: str = "data/raw", output_file: str = "output/profiling_report.json") -> Dict[str, Any]:
    """Profile all raw CSV files in data_dir and write JSON report to output_file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    report: Dict[str, Any] = {}
    
    files = {
        "transactions": "transactions.csv",
        "stores": "stores.csv",
        "products": "products.csv"
    }
    
    for key, filename in files.items():
        filepath = os.path.join(data_dir, filename)
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            report[key] = profile(df, key)
        else:
            report[key] = {"error": f"File not found: {filepath}"}
            
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        
    print(f"[Profiler] Report generated successfully at {output_file}")
    return report


if __name__ == "__main__":
    profile_raw_data()
