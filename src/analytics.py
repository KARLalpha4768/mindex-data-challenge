"""
Analytical Analytics Engine for Mindex Data Pipeline.
Executes business queries against SQLite Star Schema and outputs JSON report to output/analytics.json.
"""
import json
import os
import sqlite3
from typing import Dict, Any, List


def run_analytics(db_path: str = "output/warehouse.db", output_file: str = "output/analytics.json") -> Dict[str, Any]:
    """Execute 5 core analytical queries against warehouse.db and save JSON report."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    results: Dict[str, Any] = {}
    
    # --------------------------------------------------------------------------
    # 1. Top 5 stores by net revenue in the most recent 30-day window
    # --------------------------------------------------------------------------
    query1 = """
    WITH max_date_cte AS (
        SELECT MAX(full_date) as max_d FROM dim_date WHERE date_key IN (SELECT date_key FROM fact_sales)
    )
    SELECT 
        s.store_id,
        s.store_name,
        s.region,
        ROUND(SUM(f.line_total), 2) as net_revenue
    FROM fact_sales f
    JOIN dim_store s ON f.store_id = s.store_id
    JOIN dim_date d ON f.date_key = d.date_key
    CROSS JOIN max_date_cte m
    WHERE d.full_date >= DATE(m.max_d, '-30 days')
    GROUP BY s.store_id, s.store_name, s.region
    ORDER BY net_revenue DESC
    LIMIT 5;
    """
    cursor.execute(query1)
    results["top_5_stores_recent_30_days"] = [dict(row) for row in cursor.fetchall()]
    
    # --------------------------------------------------------------------------
    # 2. Month-over-month revenue change (%) by product category
    # --------------------------------------------------------------------------
    query2 = """
    WITH monthly_category_revenue AS (
        SELECT 
            p.category,
            d.year,
            d.month,
            d.month_name,
            ROUND(SUM(f.line_total), 2) as monthly_revenue
        FROM fact_sales f
        JOIN dim_product p ON f.product_id = p.product_id
        JOIN dim_date d ON f.date_key = d.date_key
        GROUP BY p.category, d.year, d.month, d.month_name
    ),
    mom_calc AS (
        SELECT 
            category,
            year,
            month,
            month_name,
            monthly_revenue,
            LAG(monthly_revenue) OVER (PARTITION BY category ORDER BY year, month) as prev_month_revenue
        FROM monthly_category_revenue
    )
    SELECT 
        category,
        year,
        month,
        month_name,
        monthly_revenue,
        COALESCE(prev_month_revenue, 0.0) as prev_month_revenue,
        CASE 
            WHEN prev_month_revenue IS NULL OR prev_month_revenue = 0 THEN NULL
            ELSE ROUND(((monthly_revenue - prev_month_revenue) / prev_month_revenue) * 100.0, 2)
        END as mom_growth_percentage
    FROM mom_calc
    ORDER BY category, year, month;
    """
    cursor.execute(query2)
    results["mom_revenue_change_by_category"] = [dict(row) for row in cursor.fetchall()]
    
    # --------------------------------------------------------------------------
    # 3. Return rate by store (return transactions ÷ total transactions, flag >10%)
    # --------------------------------------------------------------------------
    query3 = """
    SELECT 
        s.store_id,
        s.store_name,
        COUNT(f.sales_id) as total_transactions,
        SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END) as return_transactions,
        ROUND(CAST(SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(f.sales_id) * 100.0, 2) as return_rate_pct,
        CASE 
            WHEN (CAST(SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(f.sales_id)) > 0.10 THEN 1 
            ELSE 0 
        END as exceeds_10_pct_threshold
    FROM fact_sales f
    JOIN dim_store s ON f.store_id = s.store_id
    GROUP BY s.store_id, s.store_name
    ORDER BY return_rate_pct DESC;
    """
    cursor.execute(query3)
    results["store_return_rates"] = [dict(row) for row in cursor.fetchall()]
    
    # --------------------------------------------------------------------------
    # 4. Average transaction value by region (exclude returns)
    # --------------------------------------------------------------------------
    query4 = """
    SELECT 
        s.region,
        COUNT(f.sales_id) as valid_transaction_count,
        ROUND(SUM(f.line_total), 2) as gross_sales,
        ROUND(AVG(f.line_total), 2) as avg_transaction_value
    FROM fact_sales f
    JOIN dim_store s ON f.store_id = s.store_id
    WHERE f.is_return = 0
    GROUP BY s.region
    ORDER BY avg_transaction_value DESC;
    """
    cursor.execute(query4)
    results["avg_transaction_value_by_region"] = [dict(row) for row in cursor.fetchall()]
    
    # --------------------------------------------------------------------------
    # 5. Top 10 customers by lifetime spend (exclude guest/anonymous)
    # --------------------------------------------------------------------------
    query5 = """
    SELECT 
        f.customer_id,
        COUNT(f.sales_id) as total_order_count,
        ROUND(SUM(f.line_total), 2) as lifetime_spend,
        ROUND(AVG(f.line_total), 2) as average_order_value
    FROM fact_sales f
    WHERE f.customer_id != 'GUEST' AND f.customer_id IS NOT NULL AND f.customer_id != ''
    GROUP BY f.customer_id
    ORDER BY lifetime_spend DESC
    LIMIT 10;
    """
    cursor.execute(query5)
    results["top_10_customers_lifetime_spend"] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        
    print(f"[Analytics] Business queries executed and written to {output_file}")
    return results


if __name__ == "__main__":
    run_analytics()
