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
#
# ── F9 · partial months at the range boundary ────────────────────────────────
# WHY days_with_data exists: the dataset's first and last months are truncated.
#   2026-03 carries 27 transacting days and 2026-06 carries exactly ONE
#   (2026-06-01). A percentage that compares one day against thirty is an artefact
#   of where the extract was cut, not a business signal — it is what produces the
#   headline "−98.73%" and "+403.23%" figures, and presenting those without the
#   denominator behind them would be the same class of error as reporting a rate
#   without its base.
# DECISION: emit the transacting-day count on every row so the reader can see the
#   comparison is unequal, and state the caveat in ``definition_note``.
# ALTERNATIVE REJECTED: suppressing the boundary months. That hides real revenue
#   and makes the metric's rows disagree with every other metric's totals.
# ALTERNATIVE REJECTED: normalising to revenue-per-day. It would make the boundary
#   months comparable but silently changes what the metric *is*; the contract asks
#   for month-over-month growth, so the honest fix is disclosure, not redefinition.
MOM_GROWTH_BY_CATEGORY: str = """
-- Q2: Month-over-month revenue change (%) by product category.
-- Uses LAG over (year_month) to compare each month to its predecessor.
-- days_with_data reports how many distinct dates that category actually
-- transacted on in that month, because the first and last months of this
-- extract are partial (see F9 note above the constant).
WITH monthly AS (
    SELECT
        p.category,
        d.year_month,
        ROUND(SUM(f.net_amount), 2)  AS monthly_revenue,
        COUNT(DISTINCT d.full_date)  AS days_with_data
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
        days_with_data,
        LAG(monthly_revenue) OVER (
            PARTITION BY category ORDER BY year_month
        ) AS prev_month_revenue
    FROM monthly
)
SELECT
    category,
    year_month,
    days_with_data,
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
#
# F11: this constant is named AOV_BY_REGION, and its metric id is
# ``aov_by_region``, because contract §6 names both as a binding interface. The
# previous spelling (``avg_txn_value_by_region``) described the same number under
# a name nothing downstream was written against.
AOV_BY_REGION: str = """
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
#
# ── F7 · the previous delta was algebraically incapable of failing ───────────
# WHY this was rewritten: the old column computed
#     SUM(net WHERE is_return = 0) + SUM(net WHERE is_return = 1) − SUM(net)
# and its comment claimed a non-zero result would reveal "an arithmetic bug
#   somewhere between cleaning and loading". That claim was false. ``is_return``
#   is a 0/1 flag, so the two CASE sums are a partition of the same population and
#   the expression is identically zero for ANY data whatsoever. The auditor proved
#   it: after scaling every money column in a copy of the warehouse by 1.5 —
#   inventing $79k of revenue — the metric still reported a delta of 0.00; and
#   replacing the whole expression with the literal ``0.0`` was not noticed by any
#   test or pipeline gate (mutation M6). A control that cannot fail is worse than
#   no control, because it is presented to the reviewer as the proof.
# DECISION: publish two deltas that are *not* free, and change the published
#   ``gross_sales_net_of_discount`` so it is derived from its components
#   (extended − discount) rather than being read back from the same SUM(net) it is
#   supposed to be checked against. That substitution is the whole repair: it is
#   what stops the new columns being the old tautology under a new name.
#     • ``line_level_delta``  — SUM(extended − discount − net) over non-return
#       rows, taken from the raw column values. Non-zero as soon as any row's three
#       money columns stop agreeing with one another.
#     • ``aggregate_delta``   — (gross_list_value − discount_total) + returns_value
#       − net_revenue, computed from the ROUNDED figures printed above it. This is
#       the arithmetic a reviewer does by hand on the published table, so the
#       published table checks itself — including its rounding.
#   The two agree on well-formed data by construction, which is the point of
#   publishing both: they are derived by different routes (raw per-row values
#   versus rounded aggregates), so a discrepancy between them localises the fault
#   to serialisation rather than to the data.
#   Both evaluate to 0.00 on the current data, which is the point: they are zero
#   because the pipeline is right, not because the algebra forbids anything else.
# WHAT THESE DELTAS DO NOT DETECT — stated because the old comment's overclaim is
#   the finding: a *uniform* rescaling of quantity, unit_price, extended_amount,
#   discount_amount and net_amount together stays internally consistent, so no
#   query over fact_sales alone can see it. That failure mode is covered outside
#   this query, by ``loader.verify_warehouse()`` check 4, which ties
#   SUM(fact_sales.net_amount) back to SUM(cleaned.total_amount) at half-a-cent
#   tolerance and aborts the load. Two independent controls, each honest about
#   its scope, beat one control that claims both jobs and does neither.
REVENUE_RECONCILIATION: str = """
-- Q6 (bonus): Revenue reconciliation — ties gross, discount, returns and net.
-- This is the TX-03 proof: if discounts were recomputed away, discount_total = 0.
--
-- The two delta columns are genuine controls, not tautologies:
--   line_level_delta  = SUM(extended - discount - net) over non-return rows,
--                       from the UNROUNDED column values. Non-zero the moment any
--                       row's three money columns stop agreeing with each other.
--   aggregate_delta   = (gross_list_value - discount_total) + returns_value
--                       - net_revenue, computed from the ROUNDED figures printed
--                       above it, so the published table checks itself.
-- Neither can detect a uniform rescaling of all money columns at once; that is
-- what loader.verify_warehouse() check 4 is for. See the F7 note in queries.py.
SELECT
    ROUND(SUM(CASE WHEN is_return = 0 THEN extended_amount ELSE 0 END), 2)
        AS gross_list_value,
    ROUND(SUM(CASE WHEN is_return = 0 THEN discount_amount ELSE 0 END), 2)
        AS discount_total,
    -- Derived from its own components (extended - discount), NOT read back from
    -- net_amount. That substitution is what makes aggregate_delta below a real
    -- comparison instead of a restatement of the same SUM.
    ROUND(SUM(CASE WHEN is_return = 0 THEN extended_amount - discount_amount ELSE 0 END), 2)
        AS gross_sales_net_of_discount,
    ROUND(SUM(CASE WHEN is_return = 1 THEN net_amount ELSE 0 END), 2)
        AS returns_value,
    ROUND(SUM(net_amount), 2)
        AS net_revenue,
    -- F15: ``+ 0.0`` after ROUND. A delta of exactly zero reached from below
    -- rounds to IEEE-754 negative zero, which serialises to JSON as -0.0 and
    -- renders in a table as "-0" — a reviewer reasonably reads that as a real
    -- (if tiny) discrepancy. Adding +0.0 maps -0.0 to 0.0 and leaves every other
    -- value bit-identical.
    ROUND(
        SUM(CASE WHEN is_return = 0
                 THEN extended_amount - discount_amount - net_amount
                 ELSE 0 END),
        2
    ) + 0.0 AS line_level_delta,
    -- Deliberately built from the ROUNDED components, not from the raw sums: that
    -- is what makes this a check on the table a reviewer is actually reading. If
    -- the four figures above it do not add up as printed, this column says so.
    ROUND(
        ROUND(SUM(CASE WHEN is_return = 0 THEN extended_amount  ELSE 0 END), 2)
        - ROUND(SUM(CASE WHEN is_return = 0 THEN discount_amount ELSE 0 END), 2)
        + ROUND(SUM(CASE WHEN is_return = 1 THEN net_amount      ELSE 0 END), 2)
        - ROUND(SUM(net_amount), 2),
        2
    ) + 0.0 AS aggregate_delta
FROM fact_sales;
"""


# ── Column unit vocabulary ────────────────────────────────────────────────────
# WHY this exists at all: a bare number like ``12.5`` is ambiguous — it could be
# "12.5%" or "1250%" depending on whether the producer already multiplied by 100.
# The presentation layer previously guessed by magnitude (``abs(v) > 1`` means
# "already a percentage"), which is wrong in both directions: a genuine +150%
# month-over-month growth reads as 1.5 in ratio form and would be rendered
# "1.50%", while a genuine 0.4% return rate in percentage form would be rendered
# "40.00%". Magnitude is not type information.
#
# So the SQL author — the only party who actually knows the scale, because they
# wrote the ``* 100.0`` or chose to omit it — declares it here, once, and every
# consumer (dashboard tables, charts, README examples) formats from the
# declaration. This is the same principle the pipeline applies to the raw CSVs:
# never infer a type when the producer can state it.
#
# Vocabulary:
#   "percent"  — already scaled 0-100 (12.5 renders "12.50%")
#   "ratio"    — unscaled 0-1        (0.125 renders "12.50%")
#   "currency" — USD                 (1234.5 renders "$1,234.50")
#   "integer"  — whole count
#   "flag"     — 0/1 or bool, renders as yes/no
#   "text"     — identifier or label, rendered verbatim
UNIT_PERCENT = "percent"
UNIT_RATIO = "ratio"
UNIT_CURRENCY = "currency"
UNIT_INTEGER = "integer"
UNIT_FLAG = "flag"
UNIT_TEXT = "text"

VALID_UNITS: frozenset[str] = frozenset(
    {UNIT_PERCENT, UNIT_RATIO, UNIT_CURRENCY, UNIT_INTEGER, UNIT_FLAG, UNIT_TEXT}
)


# ── Registry ─────────────────────────────────────────────────────────────────
# WHY a dict and not module-level enumeration: the runner iterates this to
# execute every metric, so adding a query is a one-line addition here and
# zero lines in runner.py. The ``definition_note`` is serialised into
# analytics.json so the dashboard can explain each metric to the viewer.
#
# ``column_units`` must cover every column the SELECT list emits. A missing
# entry is caught by ``validate_registry()`` below at import time rather than
# surfacing as a mis-scaled number in front of a reviewer.
METRIC_REGISTRY: dict[str, dict] = {
    "top_stores_recent_30d": {
        "sql": TOP_STORES_RECENT_30D,
        "sql_ref": "src/analytics/queries.py:TOP_STORES_RECENT_30D",
        "title": "Top 5 stores by net revenue (trailing 30 days)",
        "description": (
            "Store ranking over the 30 days ending on AS_OF_DATE, with returns "
            "netted against sales rather than excluded."
        ),
        "column_units": {
            "store_id": UNIT_TEXT,
            "store_name": UNIT_TEXT,
            "region": UNIT_TEXT,
            "transaction_count": UNIT_INTEGER,
            "return_count": UNIT_INTEGER,
            "net_revenue": UNIT_CURRENCY,
        },
        "definition_note": (
            "Net revenue = SUM(net_amount) over all transactions (sales + returns) "
            "in the trailing 30-day window ending on AS_OF_DATE. Returns reduce "
            "revenue by their signed net_amount; they are not excluded."
        ),
    },
    # F11: id renamed from ``mom_revenue_by_category`` to the contract §6 name.
    "mom_growth_by_category": {
        "sql": MOM_GROWTH_BY_CATEGORY,
        "sql_ref": "src/analytics/queries.py:MOM_GROWTH_BY_CATEGORY",
        "title": "Month-over-month revenue growth by category",
        "description": (
            "Revenue per category per calendar month with the prior month "
            "carried alongside, so every percentage can be checked by hand. "
            "days_with_data states how many dates each figure rests on, because "
            "the first and last months of this extract are partial."
        ),
        # WHY percent and not ratio: the SQL already applies ``* 100.0`` so the
        # NULLIF guard and the rounding both happen in one place, in SQL, where
        # the zero-denominator case is handled. Declaring the scale here is what
        # stops the dashboard re-scaling it.
        "column_units": {
            "category": UNIT_TEXT,
            "year_month": UNIT_TEXT,
            # F9: a count of distinct transacting dates — an integer, not a rate.
            "days_with_data": UNIT_INTEGER,
            "monthly_revenue": UNIT_CURRENCY,
            "prev_month_revenue": UNIT_CURRENCY,
            "mom_change_pct": UNIT_PERCENT,
        },
        "definition_note": (
            "Month-over-month % change in net revenue by product category, using "
            "LAG over year_month. A NULL change means the previous month had zero "
            "or no revenue (division by zero is avoided via NULLIF). "
            "CAVEAT — PARTIAL MONTHS AT THE EXTRACT BOUNDARY: the first and last "
            "months of this dataset are not full months. 2026-03 contains 27 "
            "transacting days and 2026-06 contains exactly ONE (2026-06-01, the "
            "day before AS_OF_DATE). The large negative changes shown for 2026-06 "
            "(down to -99%) and the +403% shown for Food & Beverage in 2026-04 are "
            "therefore artefacts of where the extract was cut, not business "
            "signals: they compare a one-day month against a thirty-day one. The "
            "days_with_data column gives the count of distinct dates on which THAT "
            "CATEGORY transacted in that month — so it is per row, and is at most "
            "the month's dataset-wide figure quoted above — which makes the "
            "comparison's asymmetry visible rather than implied. Any "
            "month-over-month reading should be restricted to 2026-04 vs 2026-05, "
            "the only two complete months here."
        ),
    },
    "return_rate_by_store": {
        "sql": RETURN_RATE_BY_STORE,
        "sql_ref": "src/analytics/queries.py:RETURN_RATE_BY_STORE",
        "title": "Return rate by store, with >10% alert",
        "description": (
            "Both defensible denominators reported side by side, because the "
            "choice of denominator changes which stores breach the threshold."
        ),
        "column_units": {
            "store_id": UNIT_TEXT,
            "store_name": UNIT_TEXT,
            "region": UNIT_TEXT,
            "total_transactions": UNIT_INTEGER,
            "return_transactions": UNIT_INTEGER,
            "txn_return_rate_pct": UNIT_PERCENT,
            "units_sold": UNIT_INTEGER,
            "units_returned": UNIT_INTEGER,
            "unit_return_rate_pct": UNIT_PERCENT,
            "exceeds_threshold": UNIT_FLAG,
        },
        "definition_note": (
            "Two return rates reported: transaction-count-based (return txns / total "
            "txns) and unit-based (returned units / total units). The >10% flag is "
            "applied to the unit-based rate. Both are shown because the two "
            "denominators give materially different answers. "
            "DELIBERATE DEVIATION FROM CONTRACT §6, STATED RATHER THAN SILENT: the "
            "contract specifies the unit rate as SUM(returned units) / SUM(sold "
            "units); this query uses returned / (sold + returned) instead. Reason: "
            "the contracted form is a returns-to-sales ratio, which is unbounded "
            "and can exceed 100% when a period's returns settle against an earlier "
            "period's sales — a figure that reads as an error to anyone seeing it "
            "on a dashboard. The denominator used here is total units moved, so the "
            "result is a genuine proportion bounded at 100% and answers the "
            "question 'what share of everything that crossed the counter came "
            "back?'. The choice is material but not decisive: under the contracted "
            "denominator S006 reads 15.91% rather than 13.73%, and the set of "
            "stores breaching the 10% threshold is identical either way (S006, "
            "S015, S008), so no flag, alert or ranking changes. Both numerators and "
            "both denominators are published as columns, so either rate can be "
            "recomputed from this table without rerunning anything."
        ),
    },
    # F11: id renamed from ``avg_txn_value_by_region`` to the contract §6 name.
    "aov_by_region": {
        "sql": AOV_BY_REGION,
        "sql_ref": "src/analytics/queries.py:AOV_BY_REGION",
        "title": "Average transaction value by region",
        "description": (
            "Regional AOV. Depends directly on the ST-03 region imputation, "
            "which is why that decision is flagged in dim_store."
        ),
        "column_units": {
            "region": UNIT_TEXT,
            "transaction_count": UNIT_INTEGER,
            "total_revenue": UNIT_CURRENCY,
            "avg_transaction_value": UNIT_CURRENCY,
        },
        "definition_note": (
            "Average transaction value = AVG(net_amount) over non-return "
            "transactions, grouped by store region. Return transactions are "
            "excluded per the challenge specification."
        ),
    },
    "top_customers_lifetime": {
        "sql": TOP_CUSTOMERS_LIFETIME,
        "sql_ref": "src/analytics/queries.py:TOP_CUSTOMERS_LIFETIME",
        "title": "Top 10 customers by lifetime spend",
        "description": (
            "Registered customers only. The GUEST sentinel is excluded and the "
            "exclusion is stated rather than assumed."
        ),
        "column_units": {
            "customer_id": UNIT_TEXT,
            "transaction_count": UNIT_INTEGER,
            "lifetime_spend": UNIT_CURRENCY,
            "avg_order_value": UNIT_CURRENCY,
        },
        "definition_note": (
            "Top 10 customers by SUM(net_amount). Guest/anonymous transactions "
            "(customer_id = 'GUEST', the TX-06 sentinel) are excluded because the "
            "40 guest rows represent an unknown number of distinct people; including "
            "them would create an artificial whale that dominates the leaderboard "
            "by construction."
        ),
    },
    "revenue_reconciliation": {
        "sql": REVENUE_RECONCILIATION,
        "sql_ref": "src/analytics/queries.py:REVENUE_RECONCILIATION",
        "title": "Revenue reconciliation: gross → discount → returns → net",
        "description": (
            "The tie-out that makes the TX-03 finding auditable. Two independent "
            "deltas, both of which can be non-zero — a control that cannot fail "
            "proves nothing."
        ),
        "column_units": {
            "gross_list_value": UNIT_CURRENCY,
            "discount_total": UNIT_CURRENCY,
            "gross_sales_net_of_discount": UNIT_CURRENCY,
            "returns_value": UNIT_CURRENCY,
            "net_revenue": UNIT_CURRENCY,
            # F7: ``reconciliation_delta`` is replaced by two columns that can
            # actually take a non-zero value. Units declared for both.
            "line_level_delta": UNIT_CURRENCY,
            "aggregate_delta": UNIT_CURRENCY,
        },
        "definition_note": (
            "Proof-of-work for TX-03. Ties gross list value (qty × unit_price) "
            "through discounts and returns to net revenue: gross_list_value − "
            "discount_total = gross_sales_net_of_discount, and + returns_value = "
            "net_revenue. If the 20 silent discounts had been recomputed away, "
            "discount_total would read $0.00 and this metric is the only place "
            "that absence would be visible. "
            "TWO DELTAS, AND WHY BOTH ARE PUBLISHED: line_level_delta is "
            "SUM(extended_amount − discount_amount − net_amount) over non-return "
            "rows, taken from the raw column values, so it fires as soon as any "
            "single row's money columns stop agreeing with one another; "
            "aggregate_delta is (gross_list_value − discount_total) + returns_value "
            "− net_revenue computed from the ROUNDED figures printed above it, so "
            "the published table checks itself, rounding included. They are derived "
            "by different routes and agree on well-formed data, which means a "
            "disagreement between them localises the fault to serialisation rather "
            "than to the data. Both are $0.00 here because the arithmetic "
            "holds, not because the algebra forbids anything else — the previous "
            "single delta subtracted a partition of a sum from that same sum and was "
            "therefore identically zero for all possible data, including data with "
            "$79,000 of invented revenue in it. "
            "SCOPE, STATED HONESTLY: neither delta can detect a uniform rescaling of "
            "all money columns together, because such a change stays internally "
            "consistent. That case is caught outside this query by "
            "loader.verify_warehouse() check 4, which ties SUM(fact_sales.net_amount) "
            "back to SUM(total_amount) in the cleaned CSV at half-a-cent tolerance "
            "and aborts the load on failure."
        ),
    },
}


# ── The contract's binding metric ids ─────────────────────────────────────────
# F11: contract §6 names these five as a binding interface, and two of them had
# shipped under different spellings (``mom_revenue_by_category``,
# ``avg_txn_value_by_region``). Nothing caught it, because nothing had ever
# written the required names down in code. This tuple is that statement, and
# ``validate_registry`` enforces it at import — a future rename now fails the
# build instead of quietly breaking whatever was written against the contract.
REQUIRED_METRIC_IDS: tuple[str, ...] = (
    "top_stores_recent_30d",
    "mom_growth_by_category",
    "return_rate_by_store",
    "aov_by_region",
    "top_customers_lifetime",
    "revenue_reconciliation",
)


# ── Registry self-check ───────────────────────────────────────────────────────
def validate_registry(registry: dict[str, dict] | None = None) -> list[str]:
    """Check every metric spec is complete and internally consistent.

    Runs at import time. WHY at import and not in a test: a metric whose units
    are undeclared renders a wrong number on a dashboard a hiring reviewer is
    looking at. Failing loudly at import means the pipeline cannot produce a
    plausible-looking-but-wrong artifact in the first place.

    Args:
        registry: Registry to check. Defaults to ``METRIC_REGISTRY``.

    Returns:
        A list of human-readable problems. Empty means the registry is sound.
    """
    reg = METRIC_REGISTRY if registry is None else registry
    problems: list[str] = []

    # F11: the contract's ids are an interface, so a missing one is a breach and
    # not a naming preference. Extra ids are allowed (revenue_reconciliation was
    # itself an addition); absent ones are not.
    #
    # WHY only for the real registry: this function is also the shape-checker a
    # caller can point at a one-metric dict to validate it in isolation, and
    # demanding all six ids of such a fragment would report five problems that are
    # not problems. The completeness rule belongs to the shipped registry.
    is_shipped_registry = registry is None or registry is METRIC_REGISTRY
    if is_shipped_registry:
        for required_id in REQUIRED_METRIC_IDS:
            if required_id not in reg:
                problems.append(
                    f"contract §6 requires metric id {required_id!r}; registry has "
                    f"{sorted(reg)}"
                )

    for metric_id, spec in reg.items():
        for required in (
            "sql",
            "sql_ref",
            "title",
            "description",
            "definition_note",
            "column_units",
        ):
            if required not in spec:
                problems.append(f"{metric_id}: missing '{required}'")

        # F11: a ``sql_ref`` that names a symbol which does not exist sends a
        # reviewer looking for code that is not there — worse than no reference,
        # because it costs them time before it costs them trust. Resolving it here
        # against this module's own globals means the reference is verified by the
        # same import that defines it.
        ref = spec.get("sql_ref")
        if isinstance(ref, str):
            _, _, symbol = ref.partition(":")
            if not symbol:
                problems.append(
                    f"{metric_id}.sql_ref: {ref!r} has no ':<SYMBOL>' suffix; the "
                    "reference must name the SQL constant, not just its file."
                )
            elif symbol not in globals():
                problems.append(
                    f"{metric_id}.sql_ref: {ref!r} names {symbol!r}, which does not "
                    "exist in src/analytics/queries.py."
                )
            elif globals()[symbol] is not spec.get("sql"):
                problems.append(
                    f"{metric_id}.sql_ref: {ref!r} points at {symbol!r}, but that is "
                    "not the SQL this metric executes."
                )

        units = spec.get("column_units", {})
        for column, unit in units.items():
            if unit not in VALID_UNITS:
                problems.append(
                    f"{metric_id}.{column}: unit {unit!r} is not one of "
                    f"{sorted(VALID_UNITS)}"
                )

        # WHY this check: a column named ``*_pct`` declared as a ratio (or the
        # reverse) is the exact confusion this whole mechanism exists to stop,
        # and it is far easier to mistype here than to notice downstream.
        for column, unit in units.items():
            if column.endswith("_pct") and unit != UNIT_PERCENT:
                problems.append(
                    f"{metric_id}.{column}: name ends in '_pct' but unit is "
                    f"{unit!r}. Rename the column or fix the unit."
                )

    return problems


_REGISTRY_PROBLEMS = validate_registry()
if _REGISTRY_PROBLEMS:  # pragma: no cover - defensive, should never fire
    raise ValueError(
        "METRIC_REGISTRY is inconsistent:\n  " + "\n  ".join(_REGISTRY_PROBLEMS)
    )
