"""
Data Cleaning and Transformation Engine for Mindex Data Pipeline.
Validates, cleans, and standardizes raw transactions, stores, and products DataFrames.
"""
from typing import Dict, Tuple, Any
import numpy as np
import pandas as pd


def clean_products(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Clean products DataFrame."""
    df_clean = df.copy()
    audit = {}
    
    # 1. Deduplicate
    initial_len = len(df_clean)
    df_clean = df_clean.drop_duplicates(subset=["product_id"], keep="first").copy()
    audit["duplicate_products_removed"] = initial_len - len(df_clean)
    
    # 2. Handle missing category
    null_cat_count = int(df_clean["category"].isnull().sum())
    df_clean["category"] = df_clean["category"].fillna("Uncategorized")
    audit["missing_categories_imputed"] = null_cat_count
    
    # 3. Handle zero or invalid prices (impute from median category price if zero)
    zero_prices = int((df_clean["unit_price"] <= 0).sum())
    if zero_prices > 0:
        cat_medians = df_clean[df_clean["unit_price"] > 0].groupby("category")["unit_price"].median()
        overall_median = df_clean[df_clean["unit_price"] > 0]["unit_price"].median()
        
        def fix_price(row):
            if row["unit_price"] <= 0:
                return cat_medians.get(row["category"], overall_median)
            return row["unit_price"]
            
        df_clean["unit_price"] = df_clean.apply(fix_price, axis=1)
    audit["zero_prices_imputed"] = zero_prices
    
    return df_clean, audit


def clean_stores(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Clean stores DataFrame."""
    df_clean = df.copy()
    audit = {}
    
    # 1. Deduplicate store_id
    initial_len = len(df_clean)
    df_clean = df_clean.drop_duplicates(subset=["store_id"], keep="first").copy()
    audit["duplicate_stores_removed"] = initial_len - len(df_clean)
    
    # 2. Impute missing region (e.g. from state mapping or 'Unknown')
    state_to_region = {
        "NY": "East", "MA": "East", "NJ": "East",
        "CA": "West", "WA": "West", "OR": "West",
        "TX": "South", "FL": "South", "IL": "Midwest"
    }
    null_regions = int(df_clean["region"].isnull().sum())
    if null_regions > 0:
        def infer_region(row):
            if pd.isnull(row["region"]) or str(row["region"]).strip() == "":
                return state_to_region.get(str(row["state"]).upper(), "Unknown")
            return row["region"]
        df_clean["region"] = df_clean.apply(infer_region, axis=1)
    audit["missing_regions_imputed"] = null_regions
    
    # 3. Format zip codes to 5-digit string
    df_clean["zip_code"] = df_clean["zip_code"].astype(str).str.zfill(5)
    
    return df_clean, audit


def clean_transactions(
    df: pd.DataFrame,
    valid_store_ids: pd.Series,
    valid_product_ids: pd.Series
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Clean transactions DataFrame."""
    df_clean = df.copy()
    audit = {}
    
    # 1. Deduplicate transaction_id
    initial_len = len(df_clean)
    df_clean = df_clean.drop_duplicates(subset=["transaction_id"], keep="first").copy()
    audit["duplicate_transactions_removed"] = initial_len - len(df_clean)
    
    # 2. Parse dates & drop unparsable / future dates
    df_clean["transaction_date"] = pd.to_datetime(df_clean["transaction_date"], errors="coerce")
    null_dates = int(df_clean["transaction_date"].isnull().sum())
    df_clean = df_clean.dropna(subset=["transaction_date"]).copy()
    
    today_dt = pd.Timestamp.now().normalize() + pd.Timedelta(days=1)
    future_dates = int((df_clean["transaction_date"] > today_dt).sum())
    if future_dates > 0:
        df_clean = df_clean[df_clean["transaction_date"] <= today_dt].copy()
    audit["invalid_or_future_dates_removed"] = null_dates + future_dates
    
    # 3. Handle total_amount coercion
    df_clean["total_amount"] = pd.to_numeric(df_clean["total_amount"], errors="coerce")
    
    # 4. Handle zero quantity transactions
    zero_qty_count = int((df_clean["quantity"] == 0).sum())
    df_clean = df_clean[df_clean["quantity"] != 0].copy()
    audit["zero_quantity_records_removed"] = zero_qty_count
    
    # 5. Handle guest/anonymous customer_id
    null_cust_count = int(df_clean["customer_id"].isnull().sum())
    df_clean["customer_id"] = df_clean["customer_id"].fillna("GUEST")
    audit["guest_customers_imputed"] = null_cust_count
    
    # 6. Flag returns (negative quantity)
    df_clean["is_return"] = df_clean["quantity"] < 0
    
    # 7. Recalculate line total amount to ensure mathematical consistency
    # (unit_price * quantity, retaining sign for returns)
    df_clean["total_amount"] = df_clean["unit_price"] * df_clean["quantity"]
    
    # 8. Filter orphaned foreign keys (store_id and product_id)
    orphan_stores = int((~df_clean["store_id"].isin(valid_store_ids)).sum())
    orphan_products = int((~df_clean["product_id"].isin(valid_product_ids)).sum())
    
    if orphan_stores > 0:
        df_clean = df_clean[df_clean["store_id"].isin(valid_store_ids)].copy()
    if orphan_products > 0:
        df_clean = df_clean[df_clean["product_id"].isin(valid_product_ids)].copy()
        
    audit["orphan_stores_removed"] = orphan_stores
    audit["orphan_products_removed"] = orphan_products
    
    return df_clean, audit


def clean_raw_data(data_dir: str = "data/raw") -> Tuple[Dict[str, pd.DataFrame], Dict[str, Any]]:
    """Clean all 3 raw CSV datasets and return cleaned DataFrames + audit report."""
    raw_products = pd.read_csv(f"{data_dir}/products.csv")
    raw_stores = pd.read_csv(f"{data_dir}/stores.csv")
    raw_tx = pd.read_csv(f"{data_dir}/transactions.csv")
    
    clean_prod_df, prod_audit = clean_products(raw_products)
    clean_store_df, store_audit = clean_stores(raw_stores)
    clean_tx_df, tx_audit = clean_transactions(
        raw_tx,
        valid_store_ids=clean_store_df["store_id"],
        valid_product_ids=clean_prod_df["product_id"]
    )
    
    cleaned_dict = {
        "products": clean_prod_df,
        "stores": clean_store_df,
        "transactions": clean_tx_df
    }
    
    audit_summary = {
        "products": prod_audit,
        "stores": store_audit,
        "transactions": tx_audit
    }
    
    print("[Cleaner] Data cleaning completed successfully.")
    return cleaned_dict, audit_summary


if __name__ == "__main__":
    clean_raw_data()
