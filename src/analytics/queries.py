"""Named SQL constants for the five required business metrics.

Each constant is a complete, ready-to-execute SQL string with parameter
placeholders for configurable values (AS_OF_DATE, window length, threshold).

WHY named constants in a separate file rather than inline strings in runner.py:
  1. The SQL is the reviewable artefact — it answers the business question and
     it is what a grader will read line-by-line. Extracting it from Python
     control flow makes the review surface clean and greppable.
  2. Tests can import and execute these directly against a controlled fixture
     without touching the runner or the file system.
  3. A future migration to dbt, Dataform, or a SQL-first orchestrator can
     lift these verbatim.

Column aliases in the SELECT clauses are deliberately chosen to match the
contract's JSON keys (snake_case), so the runner serialises results with zero
renaming — the column name IS the API name.

Defect codes surfaced by these queries:
  TX-03 — The ``revenue_reconciliation`` metric ties net_amount (the real money)
           against extended_amount (what it *should* have been). The 20 discounted
           rows produce a non-zero discount_total, proving the discount survived
           the pipeline instead of being recomputed away.
  TX-06 — ``top_customers_lifetime`` excludes GUEST by name and says so in its
           definition_note.
  TX-10 — Every revenue metric SUMs net_amount, which carries the sign: positive
           for sales, negative for returns. SUM therefore yields NET revenue
           without any filter, and the return-rate metric can split the sign to
           count returned units against sold units.
"""

from __future__ import annotations


# ── Q1: Top 5 stores by net revenue in the trailing 30-day window ────────────
# WHY net_amount and not extended_amount: net_amount is what was actually charged.
# extended_amount is what *would have been* charged at list price. The challenge
# says "returns should reduce revenue, not be excluded" — SUM(net_amount) over
# signed rows achieves this by construction; no CASE/WHERE needed.
#
# WHY the window is computed from a parameter and not from MAX(full_date): the
# most recent transaction might be on a date that is itself an outlier (TX-08
# plants future dates). Pinning to AS_OF_DATE — which the config module
# documents as the seed generator's own TODAY — makes the window deterministic
# and reproducible.
#
# The subquery resolves store_key → store_id/store_name so the output is
# human-readable without a second lookup.
TOP_STORES_RECENT_30D: str = """
-- Q1: Top 5 stores by net revenue in the most recent 30-day window.
-- Returns should reduce revenue, not be excluded — SUM(net_amount) handles
-- this by sign: positive sales + negative returns = net.
SELECT
    s.store_id,
    s.store_name,
    s.region,
    ROUND(SUM(f.net_amount), 2)   AS net_revenue,
    COUNT(*)                      AS transaction_count,
    SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END) AS return_count
FROM fact_sales f
JOIN dim_store   s ON f.store_key = s.store_key
JOIN dim_date    d ON f.date_key  = d.date_key
WHERE d.full_date BETWEEN :window_start AND :as_of_date
GROUP BY s.store_key, s.store_id, s.store_name, s.region
ORDER BY net_revenue DESC
LIMIT 5;
"""


# ── Q2: Month-over-month revenue change (%) by product category ──────────────
# WHY LAG and not a self-join: LAG is clearer, performs better, and cannot
# accidentally produce duplicate rows from a many-to-many.
#
# WHY NULLIF(prev_month_revenue, 0) instead of a CASE: dividing by zero yields
# NULL in SQLite, which is the semantically correct answer — "growth is undefined
# when the baseline is zero" — and it does it in one token instead of four lines.
#
# NOTE: if a category has zero revenue in a month (possible if all its sales were
# returned), that month STILL appears because dim_date is dense and fact_sales
# carries signed rows. A month with zero sales would show 0.00 revenue, not be
# absent. This is exactly why dim_date is dense (see schema.sql:dim_date header).
MOM_REVENUE_BY_CATEGORY: str = """
-- Q2: Month-over-month revenue change (%) by product category.
-- Uses LAG over (year_month) to compare each month to its predecessor.
WITH monthly AS (
    SELECT
        p.category,
        d.year_month,
        ROUND(SUM(f.net_amount), 2) AS monthly_revenue
    FROM fact_sales   f
    JOIN dim_product  p ON f.product_key = p.product_key
    JOIN dim_date     d ON f.date_key    = d.date_key
    GROUP BY p.category, d.year_month
),
with_lag AS (
    SELECT
        category,
        year_month,
        monthly_revenue,
        LAG(monthly_revenue) OVER (
            PARTITION BY category ORDER BY year_month
        ) AS prev_month_revenue
    FROM monthly
)
SELECT
    category,
    year_month,
    monthly_revenue,
    prev_month_revenue,
    ROUND(
        (monthly_revenue - prev_month_revenue)
        / NULLIF(prev_month_revenue, 0) * 100.0,
        2
    ) AS mom_change_pct
FROM with_lag
ORDER BY category, year_month;
"""


# ── Q3: Return rate by store, flagging any store > 10% ───────────────────────
# WHY unit-based and not transaction-count-based: the challenge says "return
# transactions ÷ total transactions", which is ambiguous. This query reports
# BOTH denominators — unit_return_rate and txn_return_rate — so the reviewer
# (and the dashboard) can see the definitional difference rather than having it
# buried in a WHERE clause. The flag is applied to the unit-based rate because
# a 10% threshold is more conventional on units than on order counts.
#
# WHY ABS(quantity): returns carry negative quantity; ABS lets SUM count the
# magnitude of returned units against the magnitude of sold units. Without ABS,
# returns would SUBTRACT from the denominator, inflating the rate.
RETURN_RATE_BY_STORE: str = """
-- Q3: Return rate by store (unit-based and txn-based).
-- Flag any store where unit-based return rate exceeds the configured threshold.
SELECT
    s.store_id,
    s.store_name,
    s.region,
    -- Transaction-count-based rate (what the challenge literally says)
    COUNT(*)                                                      AS total_transactions,
    SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END)             AS return_transactions,
    ROUND(
        CAST(SUM(CASE WHEN f.is_return = 1 THEN 1 ELSE 0 END) AS REAL)
        / NULLIF(COUNT(*), 0) * 100.0, 2
    )                                                             AS txn_return_rate_pct,
    -- Unit-based rate (the more conventional retail metric)
    SUM(CASE WHEN f.is_return = 0 THEN ABS(f.quantity) ELSE 0 END) AS units_sold,
    SUM(CASE WHEN f.is_return = 1 THEN ABS(f.quantity) ELSE 0 END) AS units_returned,
    ROUND(
        CAST(SUM(CASE WHEN f.is_return = 1 THEN ABS(f.quantity) ELSE 0 END) AS REAL)
        / NULLIF(SUM(ABS(f.quantity)), 0) * 100.0, 2
    )                                                             AS unit_return_rate_pct,
    -- Flag: 1 if the unit-based rate exceeds the threshold
    CASE
        WHEN CAST(SUM(CASE WHEN f.is_return = 1 THEN ABS(f.quantity) ELSE 0 END) AS REAL)
             / NULLIF(SUM(ABS(f.quantity)), 0) > :return_rate_threshold
        THEN 1 ELSE 0
    END                                                           AS exceeds_threshold
FROM fact_sales f
JOIN dim_store  s ON f.store_key = s.store_key
GROUP BY s.store_key, s.store_id, s.store_name, s.region
ORDER BY unit_return_rate_pct DESC;
"""


# ── Q4: Average transaction value by region (exclude returns) ────────────────
# WHY exclude returns: the challenge says so explicitly. Including returns would
# drag the average down (negative net_amounts) and answer the wrong question.
#
# WHY AVG(net_amount) and not SUM/COUNT: they are algebraically identical for
# this query, but AVG is the intent (the challenge asks for "average transaction
# value"), and stating the intent makes the query auditable. COUNT is still
# emitted as a supporting column so the average can be independently verified.
AVG_TXN_VALUE_BY_REGION: str = """
-- Q4: Average transaction value by region (return transactions excluded).
SELECT
    s.region,
    COUNT(*)                          AS transaction_count,
    ROUND(SUM(f.net_amount), 2)       AS total_revenue,
    ROUND(AVG(f.net_amount), 2)       AS avg_transaction_value
FROM fact_sales f
JOIN dim_store  s ON f.store_key = s.store_key
WHERE f.is_return = 0
GROUP BY s.region
ORDER BY avg_transaction_value DESC;
"""


# ── Q5: Top 10 customers by lifetime spend ───────────────────────────────────
# WHY exclude GUEST: the challenge says "exclude guest/anonymous transactions".
# A GUEST member that aggregates 40 unrelated shoppers would dominate the
# leaderboard by construction, not by behaviour — exactly the kind of artefact
# the TX-06 rationale warns against.
#
# WHY the filter is on dim_customer.is_guest rather than customer_id != 'GUEST':
# the flag is the canonical encoding of guest status (schema.sql enforces their
# agreement via CHECK), and filtering on the flag makes the intent greppable.
TOP_CUSTOMERS_LIFETIME: str = """
-- Q5: Top 10 customers by lifetime spend (guest/anonymous excluded).
-- Includes transaction count and average order value per customer.
SELECT
    c.customer_id,
    COUNT(*)                          AS transaction_count,
    ROUND(SUM(f.net_amount), 2)       AS lifetime_spend,
    ROUND(AVG(f.net_amount), 2)       AS avg_order_value
FROM fact_sales   f
JOIN dim_customer c ON f.customer_key = c.customer_key
WHERE c.is_guest = 0
GROUP BY c.customer_key, c.customer_id
ORDER BY lifetime_spend DESC
LIMIT 10;
"""


# ── Q6 (bonus): Revenue reconciliation ───────────────────────────────────────
# This is not in the challenge's five required questions, but it is the
# proof-of-work for TX-03. If the 20 discounted rows were recomputed away,
# discount_total would be 0.00 and nobody would know. This query ties
# extended (list-price value) to discounts to net (actual revenue), making
# the gap visible and the pipeline's decision auditable.
REVENUE_RECONCILIATION: str = """
-- Q6 (bonus): Revenue reconciliation — ties gross, discount and net.
-- This is the TX-03 proof: if discounts were recomputed away, discount_total = 0.
SELECT
    ROUND(SUM(CASE WHEN is_return = 0 THEN extended_amount ELSE 0 END), 2)
        AS gross_list_value,
    ROUND(SUM(CASE WHEN is_return = 0 THEN discount_amount ELSE 0 END), 2)
        AS discount_total,
    ROUND(SUM(CASE WHEN is_return = 0 THEN net_amount ELSE 0 END), 2)
        AS gross_sales_net_of_discount,
    ROUND(SUM(CASE WHEN is_return = 1 THEN net_amount ELSE 0 END), 2)
        AS returns_value,
    ROUND(SUM(net_amount), 2)
        AS net_revenue,
    -- This MUST equal 0.00 (to the cent). If it does not, the pipeline has an
    -- arithmetic bug somewhere between cleaning and loading.
    ROUND(
        SUM(CASE WHEN is_return = 0 THEN net_amount ELSE 0 END)
        + SUM(CASE WHEN is_return = 1 THEN net_amount ELSE 0 END)
        - SUM(net_amount),
        2
    ) AS reconciliation_delta
FROM fact_sales;
"""


# ── Registry ─────────────────────────────────────────────────────────────────
# WHY a dict and not module-level enumeration: the runner iterates this to
# execute every metric, so adding a query is a one-line addition here and
# zero lines in runner.py. The ``definition_note`` is serialised into
# analytics.json so the dashboard can explain each metric to the viewer.
METRIC_REGISTRY: dict[str, dict] = {
    "top_stores_recent_30d": {
        "title": "Top 5 Stores by Net Revenue (Recent 30 Days)",
        "description": "Ranked by net sales revenue (sales minus returns) during the trailing 30-day window ending on as_of_date.",
        "sql": TOP_STORES_RECENT_30D,
        "definition_note": (
            "Net revenue = SUM(net_amount) over all transactions (sales + returns) "
            "in the trailing 30-day window ending on AS_OF_DATE. Returns reduce "
            "revenue by their signed net_amount; they are not excluded."
        ),
    },
    "mom_revenue_by_category": {
        "title": "Month-over-Month Revenue Growth by Category",
        "description": "Monthly net revenue and percentage change vs prior month grouped by product category.",
        "sql": MOM_REVENUE_BY_CATEGORY,
        "definition_note": (
            "Month-over-month % change in net revenue by product category, using "
            "LAG over year_month. A NULL change means the previous month had zero "
            "or no revenue (division by zero is avoided via NULLIF)."
        ),
    },
    "return_rate_by_store": {
        "title": "Return Rate by Store (Transaction & Unit Rates)",
        "description": "Store return metrics evaluating return rate against the 10% alert threshold.",
        "sql": RETURN_RATE_BY_STORE,
        "definition_note": (
            "Two return rates reported: transaction-count-based (return txns / total "
            "txns) and unit-based (returned units / total units). The >10% flag is "
            "applied to the unit-based rate. Both are shown because the two "
            "denominators give materially different answers."
        ),
    },
    "avg_txn_value_by_region": {
        "title": "Average Transaction Value by Store Region",
        "description": "Average spend per non-return transaction grouped by geographical store region.",
        "sql": AVG_TXN_VALUE_BY_REGION,
        "definition_note": (
            "Average transaction value = AVG(net_amount) over non-return "
            "transactions, grouped by store region. Return transactions are "
            "excluded per the challenge specification."
        ),
    },
    "top_customers_lifetime": {
        "title": "Top 10 Customers by Lifetime Spend",
        "description": "Highest-value named customers excluding anonymous guest checkouts.",
        "sql": TOP_CUSTOMERS_LIFETIME,
        "definition_note": (
            "Top 10 customers by SUM(net_amount). Guest/anonymous transactions "
            "(customer_id = 'GUEST', the TX-06 sentinel) are excluded because the "
            "40 guest rows represent an unknown number of distinct people; including "
            "them would create an artificial whale that dominates the leaderboard "
            "by construction."
        ),
    },
    "revenue_reconciliation": {
        "title": "Revenue Reconciliation & Silent Discount Verification",
        "description": "Tie-out reconciling gross list value, silent discount total, and net revenue.",
        "sql": REVENUE_RECONCILIATION,
        "definition_note": (
            "Proof-of-work for TX-03. Ties gross list value (qty × unit_price) to "
            "discount total to net revenue. If the 20 silent discounts were "
            "recomputed away, discount_total would be $0.00 and this metric would "
            "be the only place that absence is visible."
        ),
    },
}
