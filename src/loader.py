"""
SQLite Star Schema Loader Engine for Mindex Data Pipeline.
Creates DDL tables and loads cleaned DataFrames into output/warehouse.db.
"""
import os
import sqlite3
from typing import Dict
import pandas as pd


def create_schema(conn: sqlite3.Connection) -> None:
    """Create Star Schema DDL tables in SQLite warehouse."""
    cursor = conn.cursor()
    
    # 1. dim_date
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dim_date (
        date_key TEXT PRIMARY KEY,
        full_date DATE NOT NULL,
        year INTEGER NOT NULL,
        quarter INTEGER NOT NULL,
        month INTEGER NOT NULL,
        month_name TEXT NOT NULL,
        day INTEGER NOT NULL,
        day_of_week INTEGER NOT NULL,
        day_name TEXT NOT NULL,
        is_weekend INTEGER NOT NULL
    );
    """)
    
    # 2. dim_store
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dim_store (
        store_key INTEGER PRIMARY KEY AUTOINCREMENT,
        store_id TEXT UNIQUE NOT NULL,
        store_name TEXT NOT NULL,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        zip_code TEXT,
        region TEXT NOT NULL,
        opened_date DATE
    );
    """)
    
    # 3. dim_product
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dim_product (
        product_key INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT UNIQUE NOT NULL,
        product_name TEXT NOT NULL,
        category TEXT NOT NULL,
        unit_price REAL NOT NULL,
        supplier_id TEXT
    );
    """)
    
    # 4. fact_sales
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fact_sales (
        sales_id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT UNIQUE NOT NULL,
        date_key TEXT NOT NULL,
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        customer_id TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        line_total REAL NOT NULL,
        is_return INTEGER NOT NULL,
        FOREIGN KEY (date_key) REFERENCES dim_date(date_key),
        FOREIGN KEY (store_id) REFERENCES dim_store(store_id),
        FOREIGN KEY (product_id) REFERENCES dim_product(product_id)
    );
    """)
    
    conn.commit()


def populate_dim_date(conn: sqlite3.Connection, start_date: str, end_date: str) -> None:
    """Populate dim_date table for all calendar dates between start_date and end_date."""
    dates = pd.date_range(start=start_date, end=end_date, freq="D")
    date_df = pd.DataFrame({
        "date_key": dates.strftime("%Y%m%d"),
        "full_date": dates.strftime("%Y-%m-%d"),
        "year": dates.year,
        "quarter": dates.quarter,
        "month": dates.month,
        "month_name": dates.strftime("%B"),
        "day": dates.day,
        "day_of_week": dates.dayofweek,
        "day_name": dates.strftime("%A"),
        "is_weekend": dates.dayofweek.isin([5, 6]).astype(int)
    })
    
    date_df.to_sql("dim_date", conn, if_exists="replace", index=False)


def load_warehouse(cleaned_data: Dict[str, pd.DataFrame], db_path: str = "output/warehouse.db") -> None:
    """Load cleaned DataFrames into SQLite Star Schema database."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # Remove existing DB if fresh reload
    if os.path.exists(db_path):
        os.remove(db_path)
        
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    
    products_df = cleaned_data["products"]
    stores_df = cleaned_data["stores"]
    transactions_df = cleaned_data["transactions"]
    
    # Populate dim_date from min to max transaction dates
    min_tx_date = transactions_df["transaction_date"].min()
    max_tx_date = transactions_df["transaction_date"].max()
    populate_dim_date(conn, min_tx_date, max_tx_date)
    
    # Populate dim_store
    stores_df.to_sql("dim_store", conn, if_exists="append", index=False)
    
    # Populate dim_product
    products_df.to_sql("dim_product", conn, if_exists="append", index=False)
    
    # Format & Populate fact_sales
    fact_df = pd.DataFrame({
        "transaction_id": transactions_df["transaction_id"],
        "date_key": pd.to_datetime(transactions_df["transaction_date"]).dt.strftime("%Y%m%d"),
        "store_id": transactions_df["store_id"],
        "product_id": transactions_df["product_id"],
        "customer_id": transactions_df["customer_id"],
        "quantity": transactions_df["quantity"],
        "unit_price": transactions_df["unit_price"],
        "line_total": transactions_df["total_amount"],
        "is_return": transactions_df["is_return"].astype(int)
    })
    
    fact_df.to_sql("fact_sales", conn, if_exists="append", index=False)
    
    conn.commit()
    conn.close()
    print(f"[Loader] Star Schema database created and populated successfully at {db_path}")


if __name__ == "__main__":
    from cleaner import clean_raw_data
    cleaned_dict, _ = clean_raw_data()
    load_warehouse(cleaned_dict)
