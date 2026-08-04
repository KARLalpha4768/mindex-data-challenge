/**
 * The star schema, transcribed from CONTRACT.md §5.
 *
 * WHY THIS IS HARDCODED RATHER THAN CARRIED IN THE BUNDLE
 * -------------------------------------------------------
 * The DDL is a binding, hand-authored design decision, not a pipeline output.
 * It changes on the order of once per project, and reflecting it out of SQLite's
 * `PRAGMA table_info` would lose exactly the part a reviewer cares about — the
 * column-level *notes* explaining why each field exists. If the DDL in
 * `src/warehouse/schema.sql` changes, mirror the change here.
 */

export type KeyKind = "pk" | "fk" | "nk" | null;

export interface SchemaColumn {
  name: string;
  type: string;
  key: KeyKind;
  /** Null-only when the column is self-evident. Prefer writing a note. */
  note?: string;
  /** Highlights a column that exists specifically to expose a defect finding. */
  defect?: string;
}

export interface SchemaTable {
  name: string;
  kind: "dimension" | "fact";
  grain: string;
  purpose: string;
  columns: SchemaColumn[];
}

export const SCHEMA_TABLES: SchemaTable[] = [
  {
    name: "dim_date",
    kind: "dimension",
    grain: "One row per calendar date covered by the fact table.",
    purpose:
      "Conformed date dimension. Generated, not sourced — so month/quarter roll-ups never depend on SQLite date functions at query time.",
    columns: [
      {
        name: "date_key",
        type: "INTEGER",
        key: "pk",
        note: "yyyymmdd. A meaningful surrogate: readable in the fact table during debugging, still integer-joinable.",
      },
      { name: "full_date", type: "TEXT", key: null, note: "ISO date, the human-readable form." },
      { name: "year", type: "INTEGER", key: null },
      { name: "quarter", type: "INTEGER", key: null },
      { name: "month", type: "INTEGER", key: null },
      { name: "month_name", type: "TEXT", key: null },
      { name: "day_of_month", type: "INTEGER", key: null },
      { name: "day_of_week", type: "INTEGER", key: null },
      {
        name: "is_weekend",
        type: "INTEGER",
        key: null,
        note: "Precomputed so weekend analysis needs no CASE expression at query time.",
      },
    ],
  },
  {
    name: "dim_store",
    kind: "dimension",
    grain: "One row per surviving store_id after ST-02 survivorship.",
    purpose:
      "Store master. Carries a flag beside every value the cleaning layer altered, so any metric can be re-run excluding repaired rows.",
    columns: [
      { name: "store_key", type: "INTEGER", key: "pk", note: "Surrogate, AUTOINCREMENT." },
      {
        name: "store_id",
        type: "TEXT",
        key: "nk",
        note: "Natural key, UNIQUE NOT NULL. The UNIQUE constraint is what makes ST-02 a load-time failure rather than a silent double-count.",
        defect: "ST-02",
      },
      { name: "store_name", type: "TEXT", key: null, note: "Survivorship winner; the discarded alias lives in the audit log." },
      { name: "city", type: "TEXT", key: null },
      { name: "state", type: "TEXT", key: null },
      {
        name: "zip_code",
        type: "TEXT",
        key: null,
        note: "TEXT, never INTEGER — an integer column is what ate S003's leading zero upstream in the first place.",
        defect: "ST-01",
      },
      {
        name: "region",
        type: "TEXT",
        key: null,
        note: "Constrained to the column's own observed vocabulary: Northeast, Midwest, South, West.",
        defect: "ST-03",
      },
      {
        name: "region_is_imputed",
        type: "INTEGER",
        key: null,
        note: "1 for S013/S014. Lets AOV-by-region be recomputed on observed values only.",
        defect: "ST-03",
      },
      { name: "opened_date", type: "TEXT", key: null },
    ],
  },
  {
    name: "dim_product",
    kind: "dimension",
    grain: "One row per product_id after PR-01 dedupe and PR-02 conflict resolution.",
    purpose:
      "Product master. Type 1 today; PR-02 is the concrete argument for making it Type 2.",
    columns: [
      { name: "product_key", type: "INTEGER", key: "pk", note: "Surrogate, AUTOINCREMENT." },
      {
        name: "product_id",
        type: "TEXT",
        key: "nk",
        note: "Natural key, UNIQUE NOT NULL.",
        defect: "PR-01",
      },
      { name: "product_name", type: "TEXT", key: null },
      {
        name: "category",
        type: "TEXT",
        key: null,
        note: "'UNCATEGORIZED' sentinel for the five NULL rows. Never inferred.",
        defect: "PR-03",
      },
      { name: "category_is_imputed", type: "INTEGER", key: null, defect: "PR-03" },
      {
        name: "list_unit_price",
        type: "REAL",
        key: null,
        note: "LIST price. Deliberately distinct from fact_sales.unit_price, which is the price AS TRANSACTED. Conflating the two is how PR-02 and PR-04 leak into revenue.",
        defect: "PR-02",
      },
      {
        name: "price_is_imputed",
        type: "INTEGER",
        key: null,
        note: "Set for P005 (conflict resolved against transacted price) and P027 (zero list price preserved but suspect).",
        defect: "PR-04",
      },
      { name: "supplier_id", type: "TEXT", key: null },
    ],
  },
  {
    name: "dim_customer",
    kind: "dimension",
    grain: "One row per customer_id, plus exactly one GUEST sentinel row.",
    purpose:
      "Customer master. Exists mainly so fact_sales.customer_key can be NOT NULL without discarding the 40 guest checkouts.",
    columns: [
      { name: "customer_key", type: "INTEGER", key: "pk", note: "Surrogate, AUTOINCREMENT." },
      {
        name: "customer_id",
        type: "TEXT",
        key: "nk",
        note: "Natural key, UNIQUE NOT NULL. The literal 'GUEST' occupies one row.",
        defect: "TX-06",
      },
      {
        name: "is_guest",
        type: "INTEGER",
        key: null,
        note: "1 only on the sentinel. top_customers_lifetime filters on this; every other metric does not.",
        defect: "TX-06",
      },
    ],
  },
  {
    name: "fact_sales",
    kind: "fact",
    grain:
      "ONE ROW PER SOURCE TRANSACTION RECORD. The source is already line-level (one product per transaction row), so transaction_id is unique here and no header/line split is warranted.",
    purpose:
      "The sales fact. Three separate amount columns exist so the discount finding (TX-03) is visible in the schema itself rather than buried in a note.",
    columns: [
      { name: "sales_key", type: "INTEGER", key: "pk", note: "Surrogate, AUTOINCREMENT." },
      {
        name: "transaction_id",
        type: "TEXT",
        key: "nk",
        note: "UNIQUE. The constraint is the last line of defence behind the TX-09 dedupe.",
        defect: "TX-09",
      },
      { name: "date_key", type: "INTEGER", key: "fk", note: "-> dim_date" },
      { name: "store_key", type: "INTEGER", key: "fk", note: "-> dim_store. FK enforcement is why TX-04 rows are quarantined.", defect: "TX-04" },
      { name: "product_key", type: "INTEGER", key: "fk", note: "-> dim_product. Same for TX-05.", defect: "TX-05" },
      { name: "customer_key", type: "INTEGER", key: "fk", note: "-> dim_customer" },
      {
        name: "quantity",
        type: "INTEGER",
        key: null,
        note: "Signed. Negative on returns, so SUM() is net by construction.",
        defect: "TX-10",
      },
      {
        name: "unit_price",
        type: "REAL",
        key: null,
        note: "The price AS TRANSACTED, taken from the transaction feed — not from dim_product.",
      },
      {
        name: "extended_amount",
        type: "REAL",
        key: null,
        note: "quantity * unit_price. List value. Exists ONLY to make the discount measurable; it is never used as revenue.",
        defect: "TX-03",
      },
      {
        name: "discount_amount",
        type: "REAL",
        key: null,
        note: "extended_amount - net_amount. 0.00 on the 454 rows that reconcile.",
        defect: "TX-03",
      },
      {
        name: "net_amount",
        type: "REAL",
        key: null,
        note: "The source's own total_amount, untouched. THIS is revenue. Recomputing it is the single worst available mistake in this challenge.",
        defect: "TX-03",
      },
      { name: "is_return", type: "INTEGER", key: null, note: "1 for the 30 TX-10 rows.", defect: "TX-10" },
    ],
  },
];

/** Load-order and integrity notes rendered beside the tables. */
export const SCHEMA_NOTES: string[] = [
  "PRAGMA foreign_keys = ON for the whole load — orphan facts fail loudly instead of loading unattributed.",
  "Load order: dim_date, dim_store, dim_product, dim_customer, then fact_sales.",
  "The entire load runs inside one transaction. On any failure it rolls back, so a partial warehouse is never produced.",
  "Every dimension uses an integer surrogate key with the source business key retained as a UNIQUE natural key — so a source system renumbering its ids does not orphan history.",
];
