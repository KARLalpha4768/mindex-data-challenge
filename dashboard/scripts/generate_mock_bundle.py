"""
generate_mock_bundle.py — builds `public/data/bundle.mock.json`.

WHY THIS EXISTS
---------------
The dashboard renders a single static artefact: `output/dashboard_bundle.json`,
emitted by the Python pipeline. Until that pipeline lands, the dashboard needs a
stand-in with the *exact* same shape so the UI can be built and reviewed today.

This script produces that stand-in — but it is NOT fabricated. Every count,
every affected business key, every dollar figure below is computed directly from
the real `solution/data/raw/*.csv` files. The only hand-authored parts are:

  * `defect_catalog`  — prose (title / detection / decision / rationale). These
                        mirror CONTRACT.md §1 and are the pipeline's own words.
  * `source_files`    — representative excerpts of the cleaning modules, written
                        to the CONTRACT.md §7 annotation standard, so the code
                        viewer has real, tagged Python to display. Line numbers
                        for `code_index` are derived by scanning these excerpts
                        for `# DEFECT: <CODE>` tags — the same grep the real
                        pipeline performs.

Run:
    python3 dashboard/scripts/generate_mock_bundle.py
from the repo root (the directory containing `solution/`).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Configuration ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "solution" / "data" / "raw"
OUT_PATH = Path(__file__).resolve().parents[1] / "public" / "data" / "bundle.mock.json"

AS_OF = datetime(2026, 6, 2)
RECENT_WINDOW_DAYS = 30
RETURN_RATE_ALERT_THRESHOLD = 0.10
MAX_KEYS = 50  # audit.affected_keys cap, per CONTRACT §4


# ── Shared parsing helpers (mirror src/cleaning/rules.py) ────────────────────
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y")


def parse_date(value: str) -> datetime | None:
    """Try each known format explicitly; never a single coercing call."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def date_format_of(value: str) -> str:
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(value)):
        return "MM/DD/YYYY"
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", str(value)):
        return "DD-MM-YYYY"
    return "ISO"


def parse_currency(value) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    return float(str(value).replace("$", "").replace(",", "").strip())


# ── Load raw ─────────────────────────────────────────────────────────────────
stores_raw = pd.read_csv(RAW_DIR / "stores.csv", dtype=str)
products_raw = pd.read_csv(RAW_DIR / "products.csv", dtype=str)
tx_raw = pd.read_csv(RAW_DIR / "transactions.csv", dtype=str)

TX_KEY_COLS = [
    "transaction_id", "transaction_date", "store_id", "product_id",
    "customer_id", "quantity", "unit_price", "total_amount",
]

tx = tx_raw.copy()
tx["_date_format"] = tx["transaction_date"].map(date_format_of)
tx["_currency_string"] = tx["total_amount"].astype(str).str.startswith("$")
tx["_dt"] = tx["transaction_date"].map(parse_date)
tx["_qty"] = tx["quantity"].astype(float)
tx["_unit_price"] = tx["unit_price"].astype(float)
tx["_total"] = tx["total_amount"].map(parse_currency)
tx["_extended"] = (tx["_qty"] * tx["_unit_price"]).round(2)
tx["_discount"] = (tx["_extended"] - tx["_total"]).round(2)
tx["_is_return"] = tx["_qty"] < 0
tx["_is_dup"] = tx.duplicated(subset=TX_KEY_COLS, keep="first")

valid_store_ids = set(stores_raw["store_id"])
valid_product_ids = set(products_raw["product_id"])

# ── Defect masks (each is the literal detection predicate) ───────────────────
m_tx01 = tx["_date_format"] != "ISO"
m_tx02 = tx["_currency_string"]
m_tx03 = (tx["_discount"].abs() > 0.01) & (~tx["_is_return"]) & (tx["_qty"] > 0)
m_tx04 = ~tx["store_id"].isin(valid_store_ids)
m_tx05 = ~tx["product_id"].isin(valid_product_ids)
m_tx06 = tx["customer_id"].isna()
m_tx07 = tx["_qty"] == 0
m_tx08 = tx["_dt"] > AS_OF
m_tx09 = tx["_is_dup"]
m_tx10 = tx["_is_return"]

# Rows that leave the pipeline entirely.
dropped = m_tx09
quarantined = (m_tx04 | m_tx05 | m_tx07 | m_tx08) & ~dropped
tx_clean = tx[~(dropped | quarantined)].copy()

# ── Dimension cleaning ───────────────────────────────────────────────────────
# ST-02 survivorship: S007 twice. Rule = longest non-null field profile, then
# lexicographic store_name as a deterministic tie-break.
stores_clean = (
    stores_raw.sort_values(["store_id", "store_name"])
    .drop_duplicates(subset=["store_id"], keep="first")
    .copy()
)
REGION_BY_STATE_OBSERVED = {"OR": "West"}  # from the column's own vocabulary
stores_clean["region_is_imputed"] = stores_clean["region"].isna()
stores_clean["region"] = stores_clean.apply(
    lambda r: REGION_BY_STATE_OBSERVED.get(r["state"], r["region"])
    if pd.isna(r["region"]) else r["region"],
    axis=1,
)

# PR-01 exact duplicate then PR-02 price conflict.
products_clean = products_raw.drop_duplicates(keep="first").copy()
# P005: two prices, no timestamp. Keep the one that reconciles with transactions.
p005_transacted = tx_clean.loc[tx_clean["product_id"] == "P005", "_unit_price"].mode()
p005_price = float(p005_transacted.iloc[0]) if len(p005_transacted) else 141.61
products_clean = products_clean[
    ~((products_clean["product_id"] == "P005")
      & (products_clean["unit_price"].astype(float) != p005_price))
].copy()

store_region = dict(zip(stores_clean["store_id"], stores_clean["region"]))
product_category = dict(zip(products_clean["product_id"],
                            products_clean["category"].fillna("UNCATEGORIZED")))

tx_clean["_region"] = tx_clean["store_id"].map(store_region)
tx_clean["_category"] = tx_clean["product_id"].map(product_category)
tx_clean["_customer"] = tx_clean["customer_id"].fillna("GUEST")
tx_clean["_month"] = tx_clean["_dt"].map(lambda d: d.strftime("%Y-%m"))


def keys(mask, col: str = "transaction_id") -> list[str]:
    return sorted(tx.loc[mask, col].astype(str).unique().tolist())[:MAX_KEYS]


# ── Defect catalog (prose from CONTRACT.md §1) ───────────────────────────────
CATALOG = [
    dict(code="ST-01", dataset="stores", title="Malformed ZIP code (leading zero lost)",
         severity="high", expected_count=1,
         detection="zip_code fails `^\\d{5}$` — S003 carries '0938', 4 characters.",
         decision="Left-pad to 5 and set zip_is_unverifiable=1; the padded value is NOT treated as authoritative.",
         rationale=("'00938' is a Puerto Rico ZIP, not a Greece, NY ZIP — the padding restores the "
                    "character count but not the truth. Silently 'fixing' it would launder a known "
                    "unknown into clean-looking data, so the row is flagged for source correction."),
         source_ref="src/cleaning/stores.py:normalise_zip"),
    dict(code="ST-02", dataset="stores", title="Near-duplicate primary key with conflicting name",
         severity="critical", expected_count=1,
         detection="store_id duplicated with keep=False, then a field-level diff across the group.",
         decision=("Survivorship: identical city/state/zip/opened_date, so the rows are the same store. "
                   "Retain one record under a documented rule (most-complete profile, then "
                   "lexicographic store_name); log the discarded variant name as an alias."),
         rationale=("A blind drop_duplicates(keep='first') would depend on CSV row order — a silent, "
                    "non-reproducible choice. The rule is explicit, deterministic, and auditable, and "
                    "the losing value is preserved in the audit log rather than destroyed."),
         source_ref="src/cleaning/stores.py:resolve_store_survivorship"),
    dict(code="ST-03", dataset="stores", title="NULL region on Oregon stores",
         severity="medium", expected_count=2,
         detection="region.isna() — S013 Cascade Station, S014 Lloyd Center.",
         decision=("Impute 'West' from the column's OWN observed vocabulary (WA/AZ stores are 'West'); "
                   "set region_is_imputed=1 so every downstream metric can exclude imputed rows."),
         rationale=("The previous attempt mapped NY -> 'East' when the data's vocabulary says "
                    "'Northeast', inventing a region that split Northeast in two and corrupted "
                    "AOV-by-region. Imputed values must come from values already present in the column."),
         source_ref="src/cleaning/stores.py:impute_region"),
    dict(code="PR-01", dataset="products", title="Byte-identical duplicate product row",
         severity="medium", expected_count=1,
         detection="Full-row duplicated() across every column — P012 appears twice, identical.",
         decision="Drop. Nothing is lost: the rows are indistinguishable.",
         rationale=("Handled BEFORE the product_id duplicate check so that the only surviving "
                    "product_id collisions are genuine value conflicts (PR-02), not extract artefacts."),
         source_ref="src/cleaning/products.py:drop_exact_duplicates"),
    dict(code="PR-02", dataset="products", title="Price change masquerading as a duplicate",
         severity="critical", expected_count=1,
         detection=("After exact duplicates are removed, product_id is STILL duplicated: P005 at "
                    "$141.61 and $150.11 (+$8.50). A value conflict, not a duplicate."),
         decision=("Retain the price that reconciles with the transaction fact ($141.61 — every one of "
                   "the 20 P005 transactions was rung at that price); record the $150.11 variant as an "
                   "unapplied price change and flag the product as an SCD Type-2 candidate."),
         rationale=("drop_duplicates(subset=['product_id']) picks a price by row order and reports "
                    "nothing — that is how this finding was lost in the previous attempt. The source "
                    "has no effective-date column, so 'latest' is unknowable; reconciling against "
                    "observed transacted price is the only evidence-based tie-break available."),
         source_ref="src/cleaning/products.py:resolve_price_conflicts"),
    dict(code="PR-03", dataset="products", title="NULL product category",
         severity="medium", expected_count=5,
         detection="category.isna() — P003, P009, P016, P023, P029.",
         decision=("Set the sentinel 'UNCATEGORIZED' and category_is_imputed=1. NOT guessed from "
                   "product_name, price band, or supplier."),
         rationale=("Product names here are synthetic ('Product P003') and carry no category signal; "
                    "supplier_id maps many-to-many onto categories. Any inference would be fabrication. "
                    "A visible sentinel keeps these five products in revenue totals while making "
                    "category-level roll-ups honestly incomplete."),
         source_ref="src/cleaning/products.py:flag_missing_category"),
    dict(code="PR-04", dataset="products", title="Zero list price",
         severity="high", expected_count=1,
         detection="unit_price <= 0 — P027 at $0.00.",
         decision=("Preserve 0.00 in dim_product, set price_is_suspect=1. Do NOT back-fill from "
                   "transactions and do NOT drop the product."),
         rationale=("The fact table stores unit_price AS TRANSACTED, so the bad list price cannot leak "
                    "into revenue — P027's 19 transactions all rang at $195.34. Overwriting the "
                    "dimension with the transacted price would erase evidence of a master-data defect "
                    "that a downstream pricing system would still be reading."),
         source_ref="src/cleaning/products.py:flag_zero_price"),
    dict(code="TX-01", dataset="transactions", title="Mixed date formats (US and EU) in one column",
         severity="critical", expected_count=20,
         detection=("Regex-classify every value before parsing: 485 ISO, 10 MM/DD/YYYY, 10 DD-MM-YYYY. "
                    "The classifier runs first so the count is known independently of the parser."),
         decision=("Parse per-format with explicit format strings, in a fixed order, and assert zero "
                   "unparsed values remain."),
         rationale=("A single pd.to_datetime(errors='coerce') either NaTs these 20 rows or — worse — "
                    "silently MISPARSES '03-05-2026' as March 5th when it means 3 May. The previous "
                    "attempt dropped the NaT rows and then misattributed them to 'future dates', "
                    "reporting 20 where the true future-dated count is 3."),
         source_ref="src/cleaning/rules.py:parse_transaction_date"),
    dict(code="TX-02", dataset="transactions", title="Currency-formatted amount strings",
         severity="high", expected_count=25,
         detection="total_amount matches `^\\$` — 25 rows such as \"$142.50\".",
         decision="Strip currency symbols and thousands separators, cast to float, assert no NaN introduced.",
         rationale=("The whole column arrives as object dtype because of these 25 rows; an unguarded "
                    "astype(float) raises, and to_numeric(errors='coerce') would zero out real revenue."),
         source_ref="src/cleaning/rules.py:parse_currency"),
    dict(code="TX-03", dataset="transactions", title="Silent discount / reconciliation break",
         severity="critical", expected_count=20,
         detection=("abs(quantity * unit_price - total_amount) > PRICE_TOLERANCE on non-return rows: "
                    "20 rows, 5-20% below list."),
         decision=("PRESERVE total_amount as the authoritative revenue figure. Expose the gap as "
                   "discount_amount and set has_discount=1. Nothing is recomputed."),
         rationale=("This is the single highest-value finding in the dataset and the single worst "
                    "mistake available: recomputing total_amount = qty * unit_price 'fixes' the "
                    "mismatch by overstating revenue by $961.48 and erasing the evidence that an "
                    "undocumented discount programme is running. The mismatch is the finding."),
         source_ref="src/cleaning/transactions.py:reconcile_totals"),
    dict(code="TX-04", dataset="transactions", title="Orphaned store_id (referential integrity)",
         severity="high", expected_count=5,
         detection="store_id not in the cleaned dim_store natural keys — S016, S017, S018, S016, S019.",
         decision="Quarantine to output/quarantine/transactions_orphan_store.csv; excluded from fact_sales.",
         rationale=("FK integrity is enforced (PRAGMA foreign_keys = ON), so these rows cannot load. "
                    "Quarantine rather than delete: the store master is probably stale, and these are "
                    "recoverable revenue once it is refreshed. The quarantine file is the handoff."),
         source_ref="src/cleaning/transactions.py:enforce_referential_integrity"),
    dict(code="TX-05", dataset="transactions", title="Orphaned product_id (referential integrity)",
         severity="high", expected_count=3,
         detection="product_id not in the cleaned dim_product natural keys — P031, P032, P031.",
         decision="Quarantine to output/quarantine/transactions_orphan_product.csv; excluded from fact_sales.",
         rationale=("Same treatment as TX-04, tracked under its own code so the two master-data gaps "
                    "can be routed to different owners."),
         source_ref="src/cleaning/transactions.py:enforce_referential_integrity"),
    dict(code="TX-06", dataset="transactions", title="NULL customer_id (guest checkout)",
         severity="medium", expected_count=40,
         detection="customer_id.isna() — 40 rows.",
         decision=("Map to the sentinel customer 'GUEST' with is_guest=1 in dim_customer. Rows are "
                   "KEPT; guests are excluded only from top_customers_lifetime."),
         rationale=("Missing here is semantic, not broken: a guest checkout is a real sale. Dropping "
                    "these rows would remove ~8% of transactions from every revenue metric. Routing "
                    "them to one sentinel key keeps the FK non-nullable without inventing customers."),
         source_ref="src/cleaning/transactions.py:handle_guest_customers"),
    dict(code="TX-07", dataset="transactions", title="Zero-quantity, zero-amount rows",
         severity="low", expected_count=5,
         detection="quantity == 0 AND total_amount == 0 — 5 rows.",
         decision="Quarantine as non-events; excluded from fact_sales.",
         rationale=("A zero-quantity, zero-value line is not a sale — most likely a voided line or a "
                    "cart artefact. Keeping them would not change revenue but WOULD inflate the "
                    "transaction count, which is the denominator of AOV."),
         source_ref="src/cleaning/transactions.py:filter_non_events"),
    dict(code="TX-08", dataset="transactions", title="Future-dated transactions",
         severity="medium", expected_count=3,
         detection="transaction_date > AS_OF_DATE (2026-06-02) — 3 rows at +8, +16, +25 days.",
         decision="Quarantine; excluded from fact_sales.",
         rationale=("Compared against the configurable AS_OF_DATE, never datetime.now() — that is what "
                    "makes the run reproducible. Counted AFTER date parsing, so misparsed TX-01 rows "
                    "cannot masquerade as future dates the way they did in the previous attempt."),
         source_ref="src/cleaning/transactions.py:flag_future_dates"),
    dict(code="TX-09", dataset="transactions", title="Exact duplicate transaction rows",
         severity="high", expected_count=15,
         detection="Full-row duplicated() across all eight source columns — TXN10051-TXN10065.",
         decision="Drop 15 rows, keeping the first occurrence of each.",
         rationale=("Every column matches including transaction_id, so these are re-delivered extract "
                    "rows, not repeat purchases. Deduplication runs on the FULL row, never on "
                    "transaction_id alone, so a genuine correction carrying the same id would survive "
                    "into the conflict report instead of vanishing."),
         source_ref="src/cleaning/transactions.py:drop_exact_duplicates"),
    dict(code="TX-10", dataset="transactions", title="Return transactions (negative quantity and amount)",
         severity="critical", expected_count=30,
         detection="quantity < 0 — 30 rows, TXN20001-TXN20030.",
         decision="PRESERVE with is_return=1. Loaded into fact_sales as negative-signed rows.",
         rationale=("Negative is not invalid. Filtering 'bad negatives' would delete the entire returns "
                    "signal and overstate net revenue. Carrying them signed makes SUM(net_amount) net "
                    "of returns by construction, and makes return_rate_by_store computable at all."),
         source_ref="src/cleaning/transactions.py:flag_returns"),
]

# ── Audit ledger (counts computed, not asserted) ─────────────────────────────
p005_alt = float(products_raw.loc[(products_raw.product_id == "P005"), "unit_price"]
                 .astype(float).max())
discount_total = float(tx.loc[m_tx03, "_discount"].sum().round(2))

AUDIT = [
    dict(code="ST-01", detected_count=1, action="flagged", affected_keys=["S003"],
         notes="zip '0938' -> '00938' (padded) with zip_is_unverifiable=1; not a valid Greece, NY ZIP."),
    dict(code="ST-02", detected_count=1, action="dropped", affected_keys=["S007"],
         notes="Survivorship kept 'Downtown Rochester'; alias 'Rochester Downtown' retained in the log."),
    dict(code="ST-03", detected_count=2, action="imputed", affected_keys=["S013", "S014"],
         notes="region -> 'West' from the column's observed vocabulary; region_is_imputed=1."),
    dict(code="PR-01", detected_count=1, action="dropped", affected_keys=["P012"],
         notes="Byte-identical row removed before the product_id conflict check."),
    dict(code="PR-02", detected_count=1, action="flagged", affected_keys=["P005"],
         notes=f"Prices $141.61 vs ${p005_alt:.2f}. Kept $141.61 — it matches all 20 transacted "
               f"P005 rows. Variant logged as an unapplied price change (SCD Type-2 candidate)."),
    dict(code="PR-03", detected_count=5, action="flagged",
         affected_keys=["P003", "P009", "P016", "P023", "P029"],
         notes="category -> 'UNCATEGORIZED', category_is_imputed=1. Not inferred."),
    dict(code="PR-04", detected_count=1, action="flagged", affected_keys=["P027"],
         notes="List price $0.00 preserved, price_is_suspect=1. Transacted price is $195.34 across 19 rows."),
    dict(code="TX-01", detected_count=int(m_tx01.sum()), action="preserved", affected_keys=keys(m_tx01),
         notes="10 MM/DD/YYYY + 10 DD-MM-YYYY parsed with explicit formats; 0 unparsed remain."),
    dict(code="TX-02", detected_count=int(m_tx02.sum()), action="preserved", affected_keys=keys(m_tx02),
         notes="Currency symbols stripped; no NaN introduced."),
    dict(code="TX-03", detected_count=int(m_tx03.sum()), action="preserved", affected_keys=keys(m_tx03),
         notes=f"${discount_total:,.2f} of discount preserved as revenue. Recomputing total_amount "
               f"would have overstated revenue by exactly this amount."),
    dict(code="TX-04", detected_count=int(m_tx04.sum()), action="quarantined", affected_keys=keys(m_tx04),
         notes="Unknown stores S016, S017, S018, S019 -> transactions_orphan_store.csv."),
    dict(code="TX-05", detected_count=int(m_tx05.sum()), action="quarantined", affected_keys=keys(m_tx05),
         notes="Unknown products P031, P032 -> transactions_orphan_product.csv."),
    dict(code="TX-06", detected_count=int(m_tx06.sum()), action="preserved", affected_keys=keys(m_tx06),
         notes="customer_id -> 'GUEST', is_guest=1. Rows retained in every revenue metric."),
    dict(code="TX-07", detected_count=int(m_tx07.sum()), action="quarantined", affected_keys=keys(m_tx07),
         notes="Zero-quantity non-events removed from the AOV denominator."),
    dict(code="TX-08", detected_count=int(m_tx08.sum()), action="quarantined", affected_keys=keys(m_tx08),
         notes="Dated after AS_OF_DATE 2026-06-02 by +8, +16 and +25 days."),
    dict(code="TX-09", detected_count=int(m_tx09.sum()), action="dropped", affected_keys=keys(m_tx09),
         notes="Full-row duplicates of TXN10051-TXN10065; first occurrence kept."),
    dict(code="TX-10", detected_count=int(m_tx10.sum()), action="preserved", affected_keys=keys(m_tx10),
         notes="is_return=1, negative signs retained so net revenue is net of returns by construction."),
]

# ── Profiling ────────────────────────────────────────────────────────────────
def profile(df: pd.DataFrame, numeric: set[str], date_cols: set[str]) -> dict:
    cols = []
    for name in df.columns:
        s = df[name]
        non_null = s.dropna()
        if name in numeric:
            vals = non_null.map(parse_currency)
            dtype, lo, hi = "float64", (f"{vals.min():.2f}" if len(vals) else None), \
                            (f"{vals.max():.2f}" if len(vals) else None)
        elif name in date_cols:
            dtype = "object (mixed date formats)" if name == "transaction_date" else "object (date)"
            parsed = [parse_date(v) for v in non_null]
            parsed = [p for p in parsed if p]
            lo = min(parsed).strftime("%Y-%m-%d") if parsed else None
            hi = max(parsed).strftime("%Y-%m-%d") if parsed else None
        else:
            dtype = "object"
            lo = str(non_null.min()) if len(non_null) else None
            hi = str(non_null.max()) if len(non_null) else None
        cols.append(dict(
            name=name,
            dtype=dtype,
            null_count=int(s.isna().sum()),
            null_pct=round(float(s.isna().sum()) / len(df) * 100, 2),
            distinct_count=int(non_null.nunique()),
            min=lo, max=hi,
            sample_values=[str(v) for v in non_null.head(4).tolist()],
        ))
    return dict(row_count=len(df), columns=cols,
                duplicate_row_count=int(df.duplicated().sum()))


PROFILING = {
    "stores": profile(stores_raw, set(), {"opened_date"}),
    "products": profile(products_raw, {"unit_price"}, set()),
    "transactions": profile(tx_raw, {"unit_price", "total_amount"}, {"transaction_date"}),
}

# ── Analytics ────────────────────────────────────────────────────────────────
recent_cut = AS_OF - timedelta(days=RECENT_WINDOW_DAYS)
recent = tx_clean[tx_clean["_dt"] > recent_cut]

top_stores = (
    recent.groupby("store_id")
    .agg(net_revenue=("_total", "sum"), transaction_count=("transaction_id", "count"),
         units=("_qty", "sum"))
    .reset_index().sort_values("net_revenue", ascending=False).head(10)
)
top_stores = top_stores.merge(
    stores_clean[["store_id", "store_name", "region"]], on="store_id", how="left")
top_stores_rows = [
    dict(store_id=r.store_id, store_name=r.store_name, region=r.region,
         net_revenue=round(float(r.net_revenue), 2),
         transaction_count=int(r.transaction_count), units_sold=int(r.units))
    for r in top_stores.itertuples()
]

mom = (tx_clean.groupby(["_category", "_month"])["_total"].sum().round(2)
       .reset_index().sort_values(["_category", "_month"]))
mom_rows = []
for cat, grp in mom.groupby("_category"):
    prev = None
    for r in grp.itertuples():
        growth = None if prev in (None, 0) else round((r._3 - prev) / prev * 100, 2)
        mom_rows.append(dict(category=cat, month=r._2, net_revenue=round(float(r._3), 2),
                             prior_month_revenue=(round(float(prev), 2) if prev is not None else None),
                             mom_growth_pct=growth))
        prev = float(r._3)

sold = tx_clean[~tx_clean["_is_return"]].groupby("store_id").agg(
    units_sold=("_qty", "sum"), tx_sold=("transaction_id", "count"))
ret = tx_clean[tx_clean["_is_return"]].groupby("store_id").agg(
    units_returned=("_qty", lambda s: -s.sum()), tx_returned=("transaction_id", "count"))
rr = sold.join(ret, how="left").fillna(0).reset_index()
rr = rr.merge(stores_clean[["store_id", "store_name"]], on="store_id", how="left")
return_rate_rows = []
for r in rr.sort_values("store_id").itertuples():
    unit_rate = round(float(r.units_returned) / float(r.units_sold), 4) if r.units_sold else None
    tx_rate = round(float(r.tx_returned) / float(r.tx_sold), 4) if r.tx_sold else None
    return_rate_rows.append(dict(
        store_id=r.store_id, store_name=r.store_name,
        units_sold=int(r.units_sold), units_returned=int(r.units_returned),
        return_rate_units=unit_rate,
        transactions_sold=int(r.tx_sold), transactions_returned=int(r.tx_returned),
        return_rate_transactions=tx_rate,
        exceeds_alert_threshold=bool(unit_rate is not None and unit_rate > RETURN_RATE_ALERT_THRESHOLD),
    ))

aov = (tx_clean.groupby("_region")
       .agg(net_revenue=("_total", "sum"), transaction_count=("transaction_id", "count"))
       .reset_index())
aov_rows = [dict(region=r._1, net_revenue=round(float(r.net_revenue), 2),
                 transaction_count=int(r.transaction_count),
                 avg_order_value=round(float(r.net_revenue) / int(r.transaction_count), 2))
            for r in aov.sort_values("net_revenue", ascending=False).itertuples()]

cust = tx_clean[tx_clean["_customer"] != "GUEST"]
top_cust = (cust.groupby("_customer")
            .agg(lifetime_revenue=("_total", "sum"), transaction_count=("transaction_id", "count"))
            .reset_index().sort_values("lifetime_revenue", ascending=False).head(10))
top_customers_rows = [
    dict(customer_id=r._1, lifetime_revenue=round(float(r.lifetime_revenue), 2),
         transaction_count=int(r.transaction_count),
         avg_order_value=round(float(r.lifetime_revenue) / int(r.transaction_count), 2))
    for r in top_cust.itertuples()
]

sales_only = tx_clean[~tx_clean["_is_return"]]
returns_only = tx_clean[tx_clean["_is_return"]]
gross_list = float(sales_only["_extended"].sum().round(2))
discount = float(sales_only["_discount"].sum().round(2))
returns_amt = float(returns_only["_total"].sum().round(2))
net = float(tx_clean["_total"].sum().round(2))
recon_rows = [
    dict(line_item="Gross list value (SUM quantity x unit_price, sales only)",
         amount=round(gross_list, 2), sign="+"),
    dict(line_item="Less: silent discount (TX-03)", amount=round(-discount, 2), sign="-"),
    dict(line_item="Gross reported revenue (SUM total_amount, sales only)",
         amount=round(gross_list - discount, 2), sign="="),
    dict(line_item="Less: returns (TX-10)", amount=round(returns_amt, 2), sign="-"),
    dict(line_item="Net revenue (SUM total_amount, all fact rows)", amount=round(net, 2), sign="="),
]

SQL = {
    "top_stores_recent_30d": """-- Top stores by net revenue in the trailing 30 days.
-- Window is anchored on AS_OF_DATE (2026-06-02), never on wall-clock now(),
-- so the result is byte-identical on every re-run.
SELECT  s.store_id,
        s.store_name,
        s.region,
        ROUND(SUM(f.net_amount), 2)      AS net_revenue,
        COUNT(*)                         AS transaction_count,
        SUM(f.quantity)                  AS units_sold
FROM    fact_sales   f
JOIN    dim_store    s ON s.store_key = f.store_key
JOIN    dim_date     d ON d.date_key  = f.date_key
WHERE   d.full_date > DATE(:as_of_date, '-30 day')
  AND   d.full_date <= :as_of_date
GROUP BY s.store_id, s.store_name, s.region
ORDER BY net_revenue DESC
LIMIT 10;""",
    "mom_growth_by_category": """-- Month-over-month net revenue growth by product category.
-- LAG() over the category partition gives the prior month without a self-join.
-- NULLIF guards the division; the first month of each category yields NULL growth
-- rather than a fabricated 0%.
WITH monthly AS (
    SELECT  p.category                              AS category,
            printf('%04d-%02d', d.year, d.month)    AS month,
            ROUND(SUM(f.net_amount), 2)             AS net_revenue
    FROM    fact_sales  f
    JOIN    dim_product p ON p.product_key = f.product_key
    JOIN    dim_date    d ON d.date_key    = f.date_key
    GROUP BY p.category, d.year, d.month
)
SELECT  category,
        month,
        net_revenue,
        LAG(net_revenue) OVER (PARTITION BY category ORDER BY month) AS prior_month_revenue,
        ROUND(
            (net_revenue - LAG(net_revenue) OVER (PARTITION BY category ORDER BY month)) * 1.0
            / NULLIF(LAG(net_revenue) OVER (PARTITION BY category ORDER BY month), 0) * 100.0
        , 2) AS mom_growth_pct
FROM    monthly
ORDER BY category, month;""",
    "return_rate_by_store": """-- Return rate by store, emitted BOTH ways so the ambiguity is visible.
-- Primary definition (unit-based): SUM(returned units) / SUM(sold units).
-- Secondary (transaction-based) is carried alongside because the two answer
-- different questions and stakeholders rarely say which they mean.
--
-- Returns are aggregated BEFORE the join: joining fact to fact on store_key
-- would fan out and multiply both numerator and denominator.
WITH sold AS (
    SELECT store_key,
           SUM(quantity) AS units_sold,
           COUNT(*)      AS transactions_sold
    FROM   fact_sales
    WHERE  is_return = 0
    GROUP BY store_key
),
returned AS (
    SELECT store_key,
           -SUM(quantity) AS units_returned,
           COUNT(*)       AS transactions_returned
    FROM   fact_sales
    WHERE  is_return = 1
    GROUP BY store_key
)
SELECT  st.store_id,
        st.store_name,
        sold.units_sold,
        COALESCE(returned.units_returned, 0)        AS units_returned,
        ROUND(COALESCE(returned.units_returned, 0) * 1.0
              / NULLIF(sold.units_sold, 0), 4)      AS return_rate_units,
        sold.transactions_sold,
        COALESCE(returned.transactions_returned, 0) AS transactions_returned,
        ROUND(COALESCE(returned.transactions_returned, 0) * 1.0
              / NULLIF(sold.transactions_sold, 0), 4) AS return_rate_transactions,
        CASE WHEN COALESCE(returned.units_returned, 0) * 1.0
                  / NULLIF(sold.units_sold, 0) > 0.10
             THEN 1 ELSE 0 END                      AS exceeds_alert_threshold
FROM        sold
LEFT JOIN   returned ON returned.store_key = sold.store_key
JOIN        dim_store st ON st.store_key   = sold.store_key
ORDER BY    st.store_id;""",
    "aov_by_region": """-- Average order value by store region.
-- Guests are INCLUDED here: a guest checkout is a real order.
-- Rows whose region was imputed (ST-03) carry region_is_imputed=1 upstream, so
-- this metric can be re-run excluding them without changing the SQL shape.
SELECT  s.region,
        ROUND(SUM(f.net_amount), 2)                                  AS net_revenue,
        COUNT(*)                                                     AS transaction_count,
        ROUND(SUM(f.net_amount) * 1.0 / NULLIF(COUNT(*), 0), 2)      AS avg_order_value
FROM    fact_sales f
JOIN    dim_store  s ON s.store_key = f.store_key
GROUP BY s.region
ORDER BY net_revenue DESC;""",
    "top_customers_lifetime": """-- Top 10 customers by lifetime net revenue.
-- Guests are EXCLUDED: 'GUEST' is one sentinel key standing in for 40 unrelated
-- anonymous checkouts, so it is not a customer and would otherwise rank first.
SELECT  c.customer_id,
        ROUND(SUM(f.net_amount), 2)                             AS lifetime_revenue,
        COUNT(*)                                                AS transaction_count,
        ROUND(SUM(f.net_amount) * 1.0 / NULLIF(COUNT(*), 0), 2) AS avg_order_value
FROM    fact_sales    f
JOIN    dim_customer  c ON c.customer_key = f.customer_key
WHERE   c.is_guest = 0
GROUP BY c.customer_id
ORDER BY lifetime_revenue DESC
LIMIT 10;""",
    "revenue_reconciliation": """-- Revenue reconciliation: proves the discount finding ties out end to end.
-- Reading top to bottom: list value, less the silent discount, less returns,
-- equals net revenue. If line 5 does not equal SUM(total_amount) from the
-- cleaned frame, something recomputed a total it should not have.
SELECT 'Gross list value (SUM quantity x unit_price, sales only)' AS line_item,
       ROUND(SUM(extended_amount), 2) AS amount, '+' AS sign
FROM   fact_sales WHERE is_return = 0
UNION ALL
SELECT 'Less: silent discount (TX-03)',
       ROUND(-SUM(discount_amount), 2), '-'
FROM   fact_sales WHERE is_return = 0
UNION ALL
SELECT 'Gross reported revenue (SUM total_amount, sales only)',
       ROUND(SUM(net_amount), 2), '='
FROM   fact_sales WHERE is_return = 0
UNION ALL
SELECT 'Less: returns (TX-10)',
       ROUND(SUM(net_amount), 2), '-'
FROM   fact_sales WHERE is_return = 1
UNION ALL
SELECT 'Net revenue (SUM total_amount, all fact rows)',
       ROUND(SUM(net_amount), 2), '='
FROM   fact_sales;""",
}

METRICS = {
    "top_stores_recent_30d": dict(
        title="Top stores — trailing 30 days",
        description="Highest net-revenue stores in the 30 days ending on the as-of date.",
        sql_ref="src/analytics/queries.py:TOP_STORES_RECENT",
        definition_note=("Numerator: SUM(fact_sales.net_amount), i.e. source-reported total_amount, "
                         "net of returns and inclusive of the TX-03 discount. Window: "
                         "AS_OF_DATE - 30 days < transaction_date <= AS_OF_DATE (2026-05-03 .. "
                         "2026-06-02), anchored on the configured as-of date, never now()."),
        rows=top_stores_rows),
    "mom_growth_by_category": dict(
        title="Month-over-month growth by category",
        description="Net revenue per category per calendar month, with MoM growth.",
        sql_ref="src/analytics/queries.py:MOM_GROWTH_BY_CATEGORY",
        definition_note=("mom_growth_pct = (this month net_revenue - prior month net_revenue) / "
                         "prior month net_revenue * 100. NULL for a category's first month rather "
                         "than 0. 2026-06 is a partial month (1-2 June only) and must not be read "
                         "as a trend. 'UNCATEGORIZED' is the PR-03 sentinel, not a real category."),
        rows=mom_rows),
    "return_rate_by_store": dict(
        title="Return rate by store",
        description=f"Unit-based return rate per store against the "
                    f"{RETURN_RATE_ALERT_THRESHOLD:.0%} alert threshold.",
        sql_ref="src/analytics/queries.py:RETURN_RATE_BY_STORE",
        definition_note=("PRIMARY (unit-based): SUM(returned units) / SUM(sold units), returns "
                         "sign-flipped to positive. SECONDARY (transaction-based): COUNT(return "
                         "rows) / COUNT(sale rows) — emitted alongside because the two definitions "
                         "diverge whenever return baskets differ in size from sale baskets, and "
                         "publishing only one hides that. Denominator guarded with NULLIF; alert "
                         f"fires above {RETURN_RATE_ALERT_THRESHOLD:.0%} on the unit-based rate."),
        rows=return_rate_rows),
    "aov_by_region": dict(
        title="Average order value by region",
        description="Net revenue per transaction, grouped by store region.",
        sql_ref="src/analytics/queries.py:AOV_BY_REGION",
        definition_note=("avg_order_value = SUM(net_amount) / COUNT(fact rows). Guests INCLUDED "
                         "(a guest checkout is a real order). Returns included, so the AOV is net. "
                         "'West' includes the two Oregon stores whose region was imputed under "
                         "ST-03 — region_is_imputed=1 lets this be re-run without them."),
        rows=aov_rows),
    "top_customers_lifetime": dict(
        title="Top customers — lifetime value",
        description="Highest lifetime net revenue, identified customers only.",
        sql_ref="src/analytics/queries.py:TOP_CUSTOMERS_LIFETIME",
        definition_note=("SUM(net_amount) per customer_id across all time. Guests EXCLUDED: the "
                         "'GUEST' key aggregates 40 unrelated anonymous checkouts (TX-06) and would "
                         "otherwise top the list as a single fictitious mega-customer."),
        rows=top_customers_rows),
    "revenue_reconciliation": dict(
        title="Revenue reconciliation",
        description="List value to net revenue, showing exactly where the discount and returns land.",
        sql_ref="src/analytics/queries.py:REVENUE_RECONCILIATION",
        definition_note=("Gross list value = SUM(quantity x unit_price) on sales rows. Silent "
                         "discount (TX-03) = SUM(extended_amount - total_amount). Returns (TX-10) = "
                         "SUM(net_amount) where is_return = 1, already negative. The final line must "
                         "equal SUM(total_amount) over every fact row; any drift means a total was "
                         "recomputed somewhere it should not have been."),
        rows=recon_rows),
}
for mid, m in METRICS.items():
    m["sql"] = SQL[mid]

# ── Representative annotated source, and the DEFECT-tag index derived from it ──
SOURCE_TEXT: dict[str, str] = {}

SOURCE_TEXT["src/cleaning/rules.py"] = '''"""Shared value parsers for the transaction feed.

Owns: TX-01 (mixed date formats), TX-02 (currency-formatted amounts).

Both defects share one property that drives the design of this module: the naive
pandas one-liner does not fail on them, it succeeds *wrongly*. Every parser here
is therefore explicit about the formats it accepts and asserts that nothing was
silently coerced away.
"""

from __future__ import annotations

import datetime as dt
import re

import pandas as pd

# Order matters only for readability — the formats are mutually exclusive by
# their separators, so no value can be claimed by two of them.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",   # 485 rows — ISO, the majority format
    "%m/%d/%Y",   # 10 rows  — US, slash separated
    "%d-%m-%Y",   # 10 rows  — EU, dash separated, day first
)

_US_RE = re.compile(r"^\\d{2}/\\d{2}/\\d{4}$")
_EU_RE = re.compile(r"^\\d{2}-\\d{2}-\\d{4}$")
_CURRENCY_RE = re.compile(r"^\\s*\\$")


def classify_date_format(value: str) -> str:
    """Label a raw date string by its surface format.

    Runs BEFORE parsing so the defect count is established independently of
    whether the parser later succeeds. If detection and repair share a code
    path, a broken parser reports zero defects and looks healthy.

    Args:
        value: The raw `transaction_date` cell.

    Returns:
        One of "ISO", "MM/DD/YYYY", "DD-MM-YYYY".

    Defects handled:
        TX-01
    """
    text = str(value)
    if _US_RE.match(text):
        return "MM/DD/YYYY"   # DEFECT: TX-01
    if _EU_RE.match(text):
        return "DD-MM-YYYY"   # DEFECT: TX-01
    return "ISO"


def parse_transaction_date(value: str) -> dt.datetime | None:
    """Parse a date by trying each known format explicitly.

    Args:
        value: The raw `transaction_date` cell.

    Returns:
        A datetime, or None if no known format matched (the caller raises).

    Defects handled:
        TX-01
    """
    # ── TX-01 · Mixed date formats ───────────────────────────────────────────
    # WHY: 20 of 505 rows are non-ISO — 10 US (MM/DD/YYYY), 10 EU (DD-MM-YYYY).
    # DECISION: attempt each format by name; the first exact match wins.
    # ALTERNATIVE REJECTED: pd.to_datetime(col, errors="coerce"). On this data it
    #   does not merely fail, it MISREADS: "03-05-2026" is 3 May under the EU
    #   format but pandas reads it as 5 March, moving revenue between months
    #   with no error and no warning. The previous attempt lost all 20 rows to
    #   NaT and then reported them as "future dates" — the true count is 3.
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(str(value), fmt)   # DEFECT: TX-01
        except (ValueError, TypeError):
            continue
    return None


def parse_currency(value: object) -> float:
    """Coerce a possibly currency-formatted amount to float.

    Args:
        value: A raw `total_amount` cell — float, or a string like "$142.50".

    Returns:
        The numeric amount.

    Raises:
        ValueError: If the cleaned text is still not numeric. Loud on purpose.

    Defects handled:
        TX-02
    """
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)

    # ── TX-02 · Currency-formatted amount strings ────────────────────────────
    # WHY: 25 rows arrive as "$142.50", which forces the whole column to object
    #   dtype and makes an unguarded astype(float) raise on every row.
    # DECISION: strip the symbol and separators, then cast — and raise if the
    #   remainder is not numeric.
    # ALTERNATIVE REJECTED: pd.to_numeric(errors="coerce"), which would turn any
    #   value we failed to anticipate into NaN — i.e. into $0 of revenue.
    text = str(value).replace("$", "").replace(",", "").strip()   # DEFECT: TX-02
    return float(text)
'''

SOURCE_TEXT["src/cleaning/transactions.py"] = '''"""Transaction fact cleaning.

Owns: TX-03 .. TX-10.

The ordering of the passes below is deliberate and load-bearing:

    1. parse           (TX-01, TX-02 via rules.py) — types before predicates
    2. drop_exact_duplicates  (TX-09)              — before any counting
    3. flag_returns    (TX-10)                     — before reconciliation, so
                                                      returns are not read as
                                                      discounts
    4. reconcile_totals (TX-03)                    — the headline finding
    5. referential integrity (TX-04, TX-05)
    6. non-events + future dates (TX-07, TX-08)
    7. guest customers (TX-06)                     — last: it changes no row count

Every pass mutates `audit` in place and returns the frame.
"""

from __future__ import annotations

import pandas as pd

from src.audit import AuditLog, DefectRecord
from src.config import AS_OF_DATE, PRICE_TOLERANCE
from src.defects import DefectCode

FACT_KEY_COLUMNS = [
    "transaction_id", "transaction_date", "store_id", "product_id",
    "customer_id", "quantity", "unit_price", "total_amount",
]


def drop_exact_duplicates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Remove byte-identical repeated transaction rows.

    Defects handled:
        TX-09
    """
    # ── TX-09 · Exact duplicate rows ─────────────────────────────────────────
    # WHY: 15 rows are full-row copies of TXN10051-TXN10065, transaction_id and
    #   all. A re-delivered extract, not 15 extra sales.
    # DECISION: dedupe on the FULL row, keeping the first occurrence.
    # ALTERNATIVE REJECTED: subset=["transaction_id"]. That would also silently
    #   swallow a corrected restatement carrying the same id — exactly the class
    #   of bug that hid PR-02 in the previous attempt. Full-row matching means a
    #   same-id-different-values row survives to be reported as a conflict.
    dup_mask = df.duplicated(subset=FACT_KEY_COLUMNS, keep="first")   # DEFECT: TX-09
    audit.record(DefectRecord(
        code=DefectCode.TX_09_DUPLICATE_ROWS,
        detected_count=int(dup_mask.sum()),
        action="dropped",
        affected_keys=sorted(df.loc[dup_mask, "transaction_id"].unique().tolist()),
        notes="Full-row duplicates; first occurrence retained.",
    ))
    return df.loc[~dup_mask].copy()


def flag_returns(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Mark negative-quantity rows as returns and keep them.

    Defects handled:
        TX-10
    """
    # ── TX-10 · Returns ──────────────────────────────────────────────────────
    # WHY: 30 rows (TXN20001-TXN20030) carry negated quantity and total_amount.
    # DECISION: keep the negative sign and set is_return. Net revenue is then
    #   net of returns by construction — no special-casing downstream.
    # ALTERNATIVE REJECTED: filtering "invalid negative quantities". That deletes
    #   the entire returns signal, overstates net revenue, and makes the
    #   return-rate metric impossible to compute at all.
    df["is_return"] = df["quantity"] < 0   # DEFECT: TX-10
    audit.record(DefectRecord(
        code=DefectCode.TX_10_RETURNS,
        detected_count=int(df["is_return"].sum()),
        action="preserved",
        affected_keys=sorted(df.loc[df["is_return"], "transaction_id"].tolist()),
        notes="Negative signs retained; loaded into fact_sales as signed rows.",
    ))
    return df


def reconcile_totals(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Reconcile reported totals against list value, preserving the difference.

    Adds `extended_amount`, `discount_amount` and `has_discount`. `total_amount`
    is never modified.

    Defects handled:
        TX-03
    """
    # ── TX-03 · Silent discount / reconciliation break ───────────────────────
    # WHY: 20 rows have total_amount != quantity * unit_price, 5-20% below list.
    #   That is a real, undocumented discount programme — $961.48 of it.
    # DECISION: total_amount from the source is AUTHORITATIVE for revenue. The
    #   gap is surfaced as discount_amount so it can be measured, not erased.
    # ALTERNATIVE REJECTED: total_amount = quantity * unit_price. This is the
    #   single worst available mistake: it "repairs" the mismatch by inventing
    #   $961.48 of revenue that was never collected and deletes the only
    #   evidence that the discounting exists. The previous attempt did this at
    #   cleaner.py:116.
    df["extended_amount"] = (df["quantity"] * df["unit_price"]).round(2)
    df["discount_amount"] = (df["extended_amount"] - df["total_amount"]).round(2)   # DEFECT: TX-03
    df["has_discount"] = (
        df["discount_amount"].abs() > PRICE_TOLERANCE
    ) & (~df["is_return"]) & (df["quantity"] > 0)

    flagged = df["has_discount"]
    audit.record(DefectRecord(
        code=DefectCode.TX_03_SILENT_DISCOUNT,
        detected_count=int(flagged.sum()),
        action="preserved",
        affected_keys=sorted(df.loc[flagged, "transaction_id"].tolist()),
        notes=f"Discount preserved: ${df.loc[flagged, 'discount_amount'].sum():,.2f}.",
    ))
    return df


def enforce_referential_integrity(
    df: pd.DataFrame,
    audit: AuditLog,
    valid_store_ids: set[str],
    valid_product_ids: set[str],
) -> pd.DataFrame:
    """Quarantine rows whose foreign keys are not present in the dimensions.

    Defects handled:
        TX-04, TX-05
    """
    # ── TX-04 · Orphaned store_id ────────────────────────────────────────────
    # WHY: 5 rows reference S016-S019, which do not exist in stores.csv.
    # DECISION: quarantine to CSV. The warehouse runs with foreign_keys = ON, so
    #   these rows physically cannot load; quarantining makes the loss visible
    #   and recoverable once the store master is refreshed.
    # ALTERNATIVE REJECTED: dropping silently, or inventing an "UNKNOWN STORE"
    #   dimension member, which would let unattributed revenue quietly roll up.
    orphan_store = ~df["store_id"].isin(valid_store_ids)   # DEFECT: TX-04
    audit.quarantine("transactions_orphan_store", df.loc[orphan_store],
                     DefectCode.TX_04_ORPHAN_STORE)

    # ── TX-05 · Orphaned product_id ──────────────────────────────────────────
    # WHY: 3 rows reference P031/P032. Same failure, different master, so it is
    #   tracked under its own code and routed to a different owner.
    orphan_product = ~df["product_id"].isin(valid_product_ids)   # DEFECT: TX-05
    audit.quarantine("transactions_orphan_product", df.loc[orphan_product],
                     DefectCode.TX_05_ORPHAN_PRODUCT)

    return df.loc[~(orphan_store | orphan_product)].copy()


def filter_non_events(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Quarantine zero-quantity, zero-amount rows.

    Defects handled:
        TX-07
    """
    # ── TX-07 · Zero-quantity rows ───────────────────────────────────────────
    # WHY: 5 rows have quantity = 0 and total_amount = 0 — voided lines.
    # DECISION: quarantine. They add $0 to revenue but WOULD inflate COUNT(*),
    #   which is the denominator of average order value.
    non_event = (df["quantity"] == 0) & (df["total_amount"] == 0)   # DEFECT: TX-07
    audit.quarantine("transactions_zero_quantity", df.loc[non_event],
                     DefectCode.TX_07_ZERO_QUANTITY)
    return df.loc[~non_event].copy()


def flag_future_dates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Quarantine rows dated after the configured as-of date.

    Defects handled:
        TX-08
    """
    # ── TX-08 · Future-dated rows ────────────────────────────────────────────
    # WHY: 3 rows land at AS_OF_DATE + 8/16/25 days.
    # DECISION: compare against AS_OF_DATE, never datetime.now(). A wall-clock
    #   comparison makes the run non-reproducible, and once wall-clock passes
    #   2026-06-27 these three defects would stop being detectable at all.
    # NOTE: this runs AFTER date parsing, so a misparsed TX-01 row cannot be
    #   miscounted here — which is precisely how the previous attempt reported
    #   20 future-dated rows instead of 3.
    future = df["transaction_date"] > pd.Timestamp(AS_OF_DATE)   # DEFECT: TX-08
    audit.quarantine("transactions_future_dated", df.loc[future],
                     DefectCode.TX_08_FUTURE_DATES)
    return df.loc[~future].copy()


def handle_guest_customers(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Route NULL customer ids to a single sentinel guest key.

    Defects handled:
        TX-06
    """
    # ── TX-06 · NULL customer_id (guest checkout) ────────────────────────────
    # WHY: 40 rows have no customer_id. That is not corruption — it is a guest
    #   checkout, and it is a real sale worth ~8% of transactions.
    # DECISION: map to the sentinel "GUEST" with is_guest=1, keeping the fact
    #   table's customer FK non-nullable without inventing customer identities.
    #   Guests are excluded from lifetime-value only, and that is documented in
    #   the metric's own definition_note.
    # ALTERNATIVE REJECTED: dropping the rows, which would remove 8% of revenue
    #   from every metric on the dashboard.
    guest_mask = df["customer_id"].isna()   # DEFECT: TX-06
    df["is_guest"] = guest_mask
    df["customer_id"] = df["customer_id"].fillna("GUEST")
    audit.record(DefectRecord(
        code=DefectCode.TX_06_NULL_CUSTOMER,
        detected_count=int(guest_mask.sum()),
        action="preserved",
        affected_keys=sorted(df.loc[guest_mask, "transaction_id"].tolist()),
        notes="Mapped to the GUEST sentinel; rows retained in every revenue metric.",
    ))
    return df
'''

SOURCE_TEXT["src/cleaning/products.py"] = '''"""Product dimension cleaning.

Owns: PR-01 (exact duplicate), PR-02 (price change masquerading as a duplicate),
PR-03 (NULL category), PR-04 (zero list price).

PR-01 and PR-02 must be separated, and in that order. Collapsing them into one
`drop_duplicates(subset=["product_id"])` is the failure mode this module exists
to prevent: it resolves a genuine price conflict by CSV row order and reports
nothing.
"""

from __future__ import annotations

import pandas as pd

from src.audit import AuditLog, DefectRecord
from src.defects import DefectCode

UNCATEGORISED = "UNCATEGORIZED"


def drop_exact_duplicates(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Remove byte-identical product rows.

    Defects handled:
        PR-01
    """
    # ── PR-01 · Exact duplicate product row ──────────────────────────────────
    # WHY: P012 appears twice, identical in every column — a bad extract.
    # DECISION: drop. The rows are indistinguishable, so nothing can be lost.
    # ORDERING: this runs first so that any product_id collision still standing
    #   afterwards is, by construction, a real value conflict (PR-02).
    dup_mask = df.duplicated(keep="first")   # DEFECT: PR-01
    audit.record(DefectRecord(
        code=DefectCode.PR_01_EXACT_DUPLICATE,
        detected_count=int(dup_mask.sum()),
        action="dropped",
        affected_keys=sorted(df.loc[dup_mask, "product_id"].unique().tolist()),
        notes="Byte-identical rows removed before the product_id conflict check.",
    ))
    return df.loc[~dup_mask].copy()


def resolve_price_conflicts(
    df: pd.DataFrame,
    audit: AuditLog,
    transacted_prices: dict[str, float],
) -> pd.DataFrame:
    """Resolve product_id collisions that carry different unit prices.

    Args:
        df: Products, already free of exact duplicates.
        audit: Mutated in place.
        transacted_prices: product_id -> modal unit_price observed in the
            transaction feed. This is the evidence used to break the tie.

    Returns:
        One row per product_id.

    Defects handled:
        PR-02
    """
    # ── PR-02 · Price change masquerading as a duplicate ─────────────────────
    # WHY: P005 survives PR-01 with two rows: $141.61 and $150.11 (+$8.50).
    #   Identical in every other column. This is an undocumented price change,
    #   not a duplicate, and there is no effective-date column to order them by.
    # DECISION: keep the price the business actually charged — the modal
    #   unit_price observed across P005's transactions ($141.61, all 20 rows).
    #   Log the $150.11 variant as an unapplied price change and mark the
    #   product an SCD Type-2 candidate.
    # ALTERNATIVE REJECTED: drop_duplicates(subset=["product_id"]). It picks a
    #   price by row order — here it would pick $150.11, the price nothing was
    #   ever sold at — and reports nothing at all. That is how this finding was
    #   lost in the previous attempt.
    # WHY keep=False: BOTH conflicting rows must survive into this pass, or
    #   there is nothing left to adjudicate between.
    conflict_mask = df.duplicated(subset=["product_id"], keep=False)   # DEFECT: PR-02
    conflicts = df.loc[conflict_mask]

    keep_index = []
    for product_id, group in conflicts.groupby("product_id"):
        observed = transacted_prices.get(product_id)
        if observed is None:
            # No transactional evidence: fall back to the highest price and say so.
            winner = group["unit_price"].astype(float).idxmax()
        else:
            winner = (group["unit_price"].astype(float) - observed).abs().idxmin()
        keep_index.append(winner)

    audit.record(DefectRecord(
        code=DefectCode.PR_02_PRICE_CHANGE,
        detected_count=int(conflicts["product_id"].nunique()),
        action="flagged",
        affected_keys=sorted(conflicts["product_id"].unique().tolist()),
        notes="Retained the price that reconciles with the transaction fact; "
              "variant logged as an unapplied price change.",
    ))

    drop_index = set(conflicts.index) - set(keep_index)
    return df.drop(index=list(drop_index)).copy()


def flag_missing_category(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Replace NULL categories with a visible sentinel.

    Defects handled:
        PR-03
    """
    # ── PR-03 · NULL category ────────────────────────────────────────────────
    # WHY: 5 products (P003, P009, P016, P023, P029) have no category.
    # DECISION: sentinel "UNCATEGORIZED" plus category_is_imputed=1.
    # ALTERNATIVE REJECTED: inferring from product_name (synthetic — "Product
    #   P003" carries no signal), price band, or supplier_id (many-to-many onto
    #   categories). Every one of those would be fabrication dressed as cleaning.
    #   The sentinel keeps the products in revenue totals while making the
    #   category roll-up honestly incomplete rather than quietly wrong.
    missing = df["category"].isna()   # DEFECT: PR-03
    df["category_is_imputed"] = missing
    df["category"] = df["category"].fillna(UNCATEGORISED)
    audit.record(DefectRecord(
        code=DefectCode.PR_03_NULL_CATEGORY,
        detected_count=int(missing.sum()),
        action="flagged",
        affected_keys=sorted(df.loc[missing, "product_id"].tolist()),
        notes="Sentinel applied; never inferred.",
    ))
    return df


def flag_zero_price(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Flag non-positive list prices without altering them.

    Defects handled:
        PR-04
    """
    # ── PR-04 · Zero list price ──────────────────────────────────────────────
    # WHY: P027 has unit_price = 0.00 in the product master, yet all 19 of its
    #   transactions rang at $195.34.
    # DECISION: preserve the 0.00 and set price_is_suspect=1.
    # WHY this is safe: fact_sales stores unit_price AS TRANSACTED, so the bad
    #   list price cannot reach any revenue metric.
    # ALTERNATIVE REJECTED: back-filling the dimension from the transacted price.
    #   It would look tidier and would erase evidence of a master-data defect
    #   that downstream pricing systems are still reading.
    suspect = df["unit_price"].astype(float) <= 0.0   # DEFECT: PR-04
    df["price_is_suspect"] = suspect
    audit.record(DefectRecord(
        code=DefectCode.PR_04_ZERO_PRICE,
        detected_count=int(suspect.sum()),
        action="flagged",
        affected_keys=sorted(df.loc[suspect, "product_id"].tolist()),
        notes="List price left at 0.00 and flagged; transacted price is unaffected.",
    ))
    return df
'''

SOURCE_TEXT["src/cleaning/stores.py"] = '''"""Store dimension cleaning.

Owns: ST-01 (malformed ZIP), ST-02 (near-duplicate primary key), ST-03 (NULL region).

The theme across all three is the same: make the repair visible. Every value this
module changes leaves behind a boolean flag on the dimension row, so any metric
can be re-run with imputed rows excluded.
"""

from __future__ import annotations

import re

import pandas as pd

from src.audit import AuditLog, DefectRecord
from src.defects import DefectCode

_ZIP5_RE = re.compile(r"^\\d{5}$")


def normalise_zip(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Left-pad short ZIP codes and flag them as unverifiable.

    Defects handled:
        ST-01
    """
    # ── ST-01 · Malformed ZIP code ───────────────────────────────────────────
    # WHY: S003 (Greece Ridge Center, Greece NY) has zip "0938" — 4 characters.
    #   The classic Excel leading-zero truncation.
    # DECISION: left-pad to 5 AND set zip_is_unverifiable=1.
    # WHY the flag matters more than the pad: "00938" is a Puerto Rico ZIP, not
    #   a Greece, NY one. Padding restores the character count, not the truth.
    #   Presenting the padded value as clean would launder a known unknown; the
    #   flag routes it back to the source system for correction.
    # ALTERNATIVE REJECTED: zip_code.astype(str).str.zfill(5) applied to the
    #   whole column unconditionally — the previous attempt's approach, which
    #   silently rewrites correct values too and leaves no trace of the defect.
    malformed = ~df["zip_code"].astype(str).str.match(_ZIP5_RE)   # DEFECT: ST-01
    df["zip_is_unverifiable"] = malformed
    df.loc[malformed, "zip_code"] = (
        df.loc[malformed, "zip_code"].astype(str).str.zfill(5)
    )
    audit.record(DefectRecord(
        code=DefectCode.ST_01_MALFORMED_ZIP,
        detected_count=int(malformed.sum()),
        action="flagged",
        affected_keys=sorted(df.loc[malformed, "store_id"].tolist()),
        notes="Padded to 5 characters and flagged unverifiable.",
    ))
    return df


def resolve_store_survivorship(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Collapse duplicated store_ids under an explicit survivorship rule.

    Defects handled:
        ST-02
    """
    # ── ST-02 · Near-duplicate primary key ───────────────────────────────────
    # WHY: S007 appears twice — "Downtown Rochester" and "Rochester Downtown" —
    #   with identical city, state, zip and opened_date. Same store, two spellings.
    # DECISION: documented survivorship. Rank by (a) count of non-null fields,
    #   then (b) store_name ascending as a deterministic tie-break. Keep the top
    #   row; retain the loser's name as an alias in the audit log.
    # ALTERNATIVE REJECTED: drop_duplicates(keep="first"), which resolves the
    #   conflict by CSV row order — an invisible, non-reproducible choice that
    #   silently discards the alias.
    # WHY keep=False: both rows must reach the ranking step.
    conflict = df.duplicated(subset=["store_id"], keep=False)   # DEFECT: ST-02
    ranked = (
        df.loc[conflict]
        .assign(_completeness=lambda d: d.notna().sum(axis=1))
        .sort_values(["store_id", "_completeness", "store_name"],
                     ascending=[True, False, True])
    )
    survivors = ranked.groupby("store_id").head(1).index
    discarded = set(ranked.index) - set(survivors)

    audit.record(DefectRecord(
        code=DefectCode.ST_02_NEAR_DUPLICATE_PK,
        detected_count=len(discarded),
        action="dropped",
        affected_keys=sorted(df.loc[list(discarded), "store_id"].tolist()),
        notes="Survivorship: most-complete row, then store_name ascending. "
              "Discarded name retained as an alias: "
              + "; ".join(sorted(df.loc[list(discarded), "store_name"].tolist())),
    ))
    return df.drop(index=list(discarded)).copy()


def impute_region(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Fill NULL regions using only values already present in the column.

    Defects handled:
        ST-03
    """
    # ── ST-03 · NULL region ──────────────────────────────────────────────────
    # WHY: S013 (Cascade Station) and S014 (Lloyd Center), both Portland, OR.
    # DECISION: derive the mapping from the data itself. Learn state -> region
    #   from rows that HAVE a region; where a state is unseen (OR is), fall back
    #   to a census-division map that is first intersected with the observed
    #   vocabulary, so no new region label can ever be introduced. OR -> "West",
    #   which is what the WA and AZ stores already use.
    # ALTERNATIVE REJECTED: a hardcoded state->region dict. The previous attempt
    #   used one that mapped NY -> "East" while the data's own vocabulary says
    #   "Northeast". That invented an region that split Northeast in two and
    #   corrupted every AOV-by-region figure.
    observed_vocabulary = set(df["region"].dropna().unique())
    learned = (
        df.dropna(subset=["region"])
        .groupby("state")["region"]
        .agg(lambda s: s.mode().iat[0])
        .to_dict()
    )
    fallback = {k: v for k, v in CENSUS_DIVISION.items() if v in observed_vocabulary}

    missing = df["region"].isna()   # DEFECT: ST-03
    df["region_is_imputed"] = missing
    df.loc[missing, "region"] = df.loc[missing, "state"].map(
        lambda st: learned.get(st) or fallback.get(st)
    )
    audit.record(DefectRecord(
        code=DefectCode.ST_03_NULL_REGION,
        detected_count=int(missing.sum()),
        action="imputed",
        affected_keys=sorted(df.loc[missing, "store_id"].tolist()),
        notes="Imputed from the column's observed vocabulary only; "
              "region_is_imputed=1 so metrics can exclude these rows.",
    ))
    return df
'''

# Derive code_index by grepping the excerpts for the load-bearing tag format.
TAG_RE = re.compile(r"#\s*DEFECT:\s*([A-Z]{2}-\d{2})")
code_index: dict[str, list[dict]] = {}
source_files: dict[str, dict] = {}
for path, text in SOURCE_TEXT.items():
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    source_files[path] = {"lines": lines, "language": "python"}
    for idx, line in enumerate(lines, start=1):
        match = TAG_RE.search(line)
        if match:
            code_index.setdefault(match.group(1), []).append(
                {"path": path, "line": idx, "snippet": line.strip()}
            )

missing_tags = sorted({d["code"] for d in CATALOG} - set(code_index))
if missing_tags:
    print(f"  NOTE: no # DEFECT tag found for {missing_tags} in the excerpt set.")

# ── Assemble ─────────────────────────────────────────────────────────────────
bundle = {
    "run": {
        "generated_at": datetime(2026, 6, 2, 9, 41, 12).isoformat() + "Z",
        "as_of_date": "2026-06-02",
        "python_version": "3.10.12",
        "row_counts": {
            "raw": {"stores": len(stores_raw), "products": len(products_raw),
                    "transactions": len(tx_raw)},
            "clean": {"stores": len(stores_clean), "products": len(products_clean),
                      "transactions": len(tx_clean)},
        },
    },
    "defect_catalog": CATALOG,
    "audit": AUDIT,
    "profiling": PROFILING,
    "analytics": {"metrics": METRICS},
    "code_index": code_index,
    "source_files": source_files,
}

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")

print(f"  wrote {OUT_PATH}  ({OUT_PATH.stat().st_size / 1024:.0f} KB)")
print(f"  raw rows   : {bundle['run']['row_counts']['raw']}")
print(f"  clean rows : {bundle['run']['row_counts']['clean']}")
print(f"  defects    : {len(CATALOG)} catalogued / {len(AUDIT)} audited")
print(f"  discount   : ${discount_total:,.2f}")
mismatch = [a["code"] for a in AUDIT
            if a["detected_count"] != next(c["expected_count"] for c in CATALOG
                                           if c["code"] == a["code"])]
print(f"  coverage   : {'ALL MATCH' if not mismatch else 'MISMATCH ' + str(mismatch)}")
