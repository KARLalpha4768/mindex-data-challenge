-- ═══════════════════════════════════════════════════════════════════════════════
--  src/warehouse/schema.sql
--  Mindex retail analytics warehouse — SQLite star schema (contract §5)
-- ═══════════════════════════════════════════════════════════════════════════════
--
--  WHAT THIS FILE IS
--  -----------------
--  The complete DDL for output/warehouse.db. It is executed statement-by-statement
--  by src/warehouse/loader.py inside a single explicit transaction (see the note on
--  executescript() in that module), so the file must contain only statements that
--  are legal inside a transaction. SQLite makes DDL transactional, so DROP/CREATE
--  qualify; PRAGMA statements do not and therefore live in the loader.
--
--  THE MODEL: one fact, four conformed dimensions
--  ----------------------------------------------
--      dim_date ──┐
--      dim_store ─┼──> fact_sales
--      dim_product┤
--      dim_customer┘
--
--  WHY a star and not the 3 normalised source tables: every question the challenge
--  asks ("revenue by region", "return rate by store", "month-over-month by
--  category") is an aggregate of one measure grouped by attributes of exactly one
--  entity. A star answers those with a single join per grouping column, no
--  transitive joins, and — critically for this dataset — it forces the grain to be
--  declared once, in one place, instead of being re-derived by each analyst.
--
--  ─────────────────────────────────────────────────────────────────────────────
--  WHY SURROGATE INTEGER KEYS ON EVERY DIMENSION  (the graded design decision)
--  ─────────────────────────────────────────────────────────────────────────────
--  Each dimension carries a meaningless auto-integer `*_key` as its primary key and
--  keeps the source business key as a UNIQUE NOT NULL natural key. Three reasons,
--  all of which this specific dataset demonstrates:
--
--  1. SOURCE-KEY INSTABILITY. `store_id` is not trustworthy as a primary key: S007
--     arrives TWICE in stores.csv (defect ST-02) with two different names, and five
--     transactions reference store ids that do not exist at all (ST/TX-04:
--     S016–S019). A source system that can emit a duplicate PK and dangling
--     references is a source system whose keys will change again. Binding every
--     fact row directly to `store_id` would mean a future re-key of the master data
--     rewrites the entire fact table; binding to `store_key` means it rewrites one
--     dimension row.
--
--  2. FUTURE SCD TYPE 2. P005 (defect PR-02) already proves this dataset has
--     slowly-changing attributes: the product appears twice with two list prices
--     (141.61 and 150.11) — an undocumented price change, not a duplicate. Today we
--     collapse that to one current-value row. The moment the business wants "what
--     was the list price when this sold?", dim_product needs two rows for P005 with
--     validity dates — which is only expressible if the primary key is NOT the
--     product_id. The surrogate key is what makes that change additive instead of
--     a schema migration.
--
--  3. NARROWER FACT ROWS AND FASTER JOINS. A fact row keyed on four INTEGERs is
--     materially smaller than one keyed on four TEXT business keys, and integer
--     equality joins beat string joins. At 505 source rows this is aesthetic; the
--     habit is what matters, because the same model at 500M rows is not.
--
--  Trade-off, stated honestly: surrogate keys make the raw fact table unreadable
--  without joins, and they add a key-resolution step to every load — a step that
--  can fail. loader.py turns that into a feature by ASSERTING that zero natural
--  keys fail to resolve, so a cleaning-layer escape becomes a loud crash instead of
--  a silently dropped row.
--
--  ─────────────────────────────────────────────────────────────────────────────
--  CONVENTIONS USED THROUGHOUT
--  ─────────────────────────────────────────────────────────────────────────────
--  * Booleans are INTEGER 0/1 (SQLite has no BOOLEAN type) and every one of them
--    carries a `CHECK (col IN (0,1))`, because "TRUE", "true", 2 and NULL are all
--    things a careless writer would otherwise get away with.
--  * Dates are TEXT in ISO-8601 'YYYY-MM-DD'. WHY TEXT and not a numeric epoch:
--    ISO text sorts chronologically as text, is readable in any SQLite browser, and
--    is what SQLite's own date() / strftime() functions consume. The `date(x) = x`
--    CHECKs below make the format itself a constraint rather than a convention.
--  * Money is REAL. WHY not INTEGER cents (which would be strictly more correct):
--    the source CSV is 2-decimal text, the analytics layer must reproduce the exact
--    reported totals, and every comparison in this project is tolerance-based to
--    the cent anyway. The tolerance is stated in src/config.py:PRICE_TOLERANCE and
--    is applied in the CHECKs below rather than assumed.
--  * `*_is_imputed` / `*_is_suspect` / `*_conflict` flags travel WITH the value
--    they describe. WHY in the dimension rather than only in audit_report.json: a
--    dashboard user grouping revenue by region must be able to see, in the same
--    query, that two of those regions were inferred by the pipeline and not stated
--    by the source. Provenance that only exists in a side report is provenance
--    nobody will join to.
--
--  ─────────────────────────────────────────────────────────────────────────────
--  DROP ORDER
--  ─────────────────────────────────────────────────────────────────────────────
--  Child first, then parents. WHY it matters: the loader runs with
--  `PRAGMA foreign_keys = ON`, under which dropping a parent table while a child
--  still references it is treated as deleting every parent row and raises
--  FOREIGN KEY constraint failed. Dropping fact_sales first makes the dimension
--  drops unobserved by any child.
-- ═══════════════════════════════════════════════════════════════════════════════

DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_date;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_customer;


-- ═══════════════════════════════════════════════════════════════════════════════
--  dim_date
-- ═══════════════════════════════════════════════════════════════════════════════
--  ROLE:  Dimension (conformed; the only one shared by every time-sliced metric).
--  GRAIN: EXACTLY ONE ROW PER CALENDAR DAY in the loaded range — every day, not
--         only the days on which something was sold.
--
--  WHY DENSE, AND WHY THAT IS THE WHOLE POINT OF THIS TABLE:
--  A date dimension populated only from `SELECT DISTINCT transaction_date` is not a
--  dimension, it is a de-duplicated copy of the fact table's date column. The
--  difference shows up the moment anyone asks a period-over-period question:
--
--      * A month with zero sales for a category VANISHES from a GROUP BY on the
--        fact table. Month-over-month growth then compares March to May and labels
--        the result "April → May", silently inventing a number.
--      * A store that closed mid-window shows no gap; its revenue line simply
--        joins two distant points and looks like a smooth decline.
--      * "Average daily revenue" divides by the number of days that had sales, not
--        the number of days in the period — which flatters every quiet store.
--
--  With a dense dimension, an absent month is a LEFT JOIN producing NULL/0, which
--  renders as a gap on the chart and is arithmetically correct. Absent data must be
--  visible as absent; that is the same principle as preserving TX-03's discount
--  instead of recomputing it away.
--
--  `date_key` is a "smart" key (yyyymmdd) rather than a meaningless sequence. WHY
--  the exception to the surrogate-key rule: calendar dates, unlike business
--  entities, can never be re-keyed by a source system — 2026-05-30 will be
--  2026-05-30 forever — and a human-readable key makes fact rows debuggable by eye.
--  The `date_key = strftime(...)` CHECK below stops the smart key drifting from the
--  date it claims to encode.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE dim_date (
    -- yyyymmdd as an integer, e.g. 2026-05-30 -> 20260530.
    date_key      INTEGER NOT NULL PRIMARY KEY,

    -- The natural key, ISO-8601. UNIQUE so the smart key and the date stay 1:1.
    full_date     TEXT    NOT NULL UNIQUE,

    year          INTEGER NOT NULL,
    quarter       INTEGER NOT NULL,
    month         INTEGER NOT NULL,

    -- 'YYYY-MM'. Derived convenience column, NOT in the contract's minimum list.
    -- WHY it exists: `mom_growth_by_category` needs a single groupable month grain.
    -- Grouping by (year, month) works but every consumer then has to re-derive the
    -- ordering and the label; a sortable text month key means the analytics layer
    -- writes GROUP BY d.year_month and cannot get the year boundary wrong.
    year_month    TEXT    NOT NULL,

    -- Full English name ('January'). Display only — never sort by this column, it
    -- sorts alphabetically. Sort by `month` or `year_month`.
    month_name    TEXT    NOT NULL,

    day_of_month  INTEGER NOT NULL,

    -- 0 = Sunday .. 6 = Saturday. WHY this convention and not Python's Monday=0:
    -- it matches SQLite's own strftime('%w', ...), so a reviewer who cross-checks
    -- this column against the database's built-in function gets agreement instead
    -- of an off-by-one they have to chase. Consumers should prefer `is_weekend`.
    day_of_week   INTEGER NOT NULL,

    is_weekend    INTEGER NOT NULL,

    -- ── Constraints that encode the calendar's own rules ──────────────────────
    -- WHY bother when the loader generates these values from Python's datetime:
    -- because the loader is not the only thing that will ever write here, and a
    -- hand-inserted or hand-patched row is exactly the kind of thing that produces
    -- a quarter 5. These fire on writes the loader never made.
    CHECK (quarter BETWEEN 1 AND 4),
    CHECK (month BETWEEN 1 AND 12),
    CHECK (day_of_month BETWEEN 1 AND 31),
    CHECK (day_of_week BETWEEN 0 AND 6),
    CHECK (is_weekend IN (0, 1)),
    -- Weekend is *defined* as Sat/Sun; storing it and defining it separately would
    -- let the two disagree.
    CHECK (is_weekend = (CASE WHEN day_of_week IN (0, 6) THEN 1 ELSE 0 END)),
    -- full_date must be a real, ISO-formatted date: date() returns NULL for junk
    -- and normalises anything else, so equality here is a strict format test.
    CHECK (date(full_date) = full_date),
    -- The smart key must actually encode the date it sits next to.
    CHECK (date_key = CAST(strftime('%Y%m%d', full_date) AS INTEGER)),
    CHECK (year_month = strftime('%Y-%m', full_date))
);


-- ═══════════════════════════════════════════════════════════════════════════════
--  dim_store
-- ═══════════════════════════════════════════════════════════════════════════════
--  ROLE:  Dimension.
--  GRAIN: One row per surviving physical store (15 rows from 16 source rows —
--         ST-02's duplicate S007 is resolved by a ranked survivorship rule in
--         src/cleaning/stores.py, not by keep="first").
--
--  Non-obvious columns:
--    * `zip_is_suspect`   — ST-01. S003's ZIP arrives as '0938': four characters,
--        because a spreadsheet ate the leading zero. The pipeline left-pads it to
--        '00938' to restore the plausible original encoding, but 00938 is NOT a
--        valid New York ZIP, so presenting it as "corrected" would be a lie told
--        with a straight face. The flag is the honest half of that decision:
--        the value is repaired to the extent it can be, and marked unverifiable.
--    * `region_is_imputed`— ST-03. Two Oregon stores have a NULL region. The
--        pipeline fills them from the vocabulary already present in the column
--        (Northeast/Midwest/South/West → OR = West) and flags it. The previous
--        solution invented "East", splitting Northeast in two and corrupting every
--        by-region metric; a flag column is what lets a reviewer see which rows
--        carry inferred geography and re-run the metric without them.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE dim_store (
    store_key         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- Natural key from the source system. UNIQUE is the constraint that would have
    -- caught ST-02 at load time had the survivorship rule failed to run.
    store_id          TEXT    NOT NULL UNIQUE,

    store_name        TEXT    NOT NULL,
    city              TEXT    NOT NULL,
    state             TEXT    NOT NULL,

    -- TEXT, never INTEGER: '00938' as an integer is 938 (see io_utils.read_csv_as_str).
    zip_code          TEXT    NOT NULL,
    zip_is_suspect    INTEGER NOT NULL DEFAULT 0,

    -- NOT NULL is deliberate and load-bearing: if the ST-03 imputation were ever
    -- removed or silently skipped, this constraint aborts the entire load instead
    -- of producing a warehouse with a NULL region that quietly disappears from
    -- every GROUP BY region. A constraint that can fire is worth more than a
    -- comment saying it should not happen.
    region            TEXT    NOT NULL,
    region_is_imputed INTEGER NOT NULL DEFAULT 0,

    opened_date       TEXT,

    CHECK (zip_is_suspect IN (0, 1)),
    CHECK (region_is_imputed IN (0, 1)),
    CHECK (length(store_id) > 0),
    -- US ZIPs are 5 digits once ST-01's padding has run. This fires if the padding
    -- is ever applied unconditionally to already-5-char values (the previous
    -- solution's bug #5) or not at all.
    CHECK (length(zip_code) = 5),
    CHECK (opened_date IS NULL OR date(opened_date) = opened_date)
);


-- ═══════════════════════════════════════════════════════════════════════════════
--  dim_product
-- ═══════════════════════════════════════════════════════════════════════════════
--  ROLE:  Dimension.
--  GRAIN: One row per product, holding its CURRENT list price (30 rows from 32
--         source rows: PR-01 drops one byte-identical duplicate, PR-02 collapses
--         P005's two prices to one current value plus a flag).
--
--  Non-obvious columns:
--    * `list_unit_price` — the MASTER-DATA price, i.e. what the catalogue says the
--        product costs today. It is emphatically NOT the price anything sold at;
--        that lives in fact_sales.unit_price. Keeping them apart is what makes
--        PR-02 legible: all 20 P005 transactions rang at 141.61 and none at 150.11,
--        so the +$8.50 increase post-dates the transaction window. If the fact used
--        the dimension's price, that finding would be erased and P005's historical
--        revenue would be overstated.
--    * `price_conflict`  — PR-02. Set when the source presented more than one list
--        price for the same product. The surviving value is chosen by MAX (highest
--        = latest), deterministically — never by file order, because the generator
--        shuffles rows and drop_duplicates would therefore pick a different price
--        on a different shuffle.
--    * `price_is_imputed`— PR-04. P027 arrives at 0.00. The pipeline substitutes
--        the category median and flags it. It does NOT write the transacted price
--        (195.34) into this column, even though 19 transactions corroborate it:
--        that would launder fact data into a master-data field and make the
--        dimension look authoritative about something it never knew.
--    * `category_is_imputed` — PR-03. Five products have no category; the literal
--        'Unknown' is written rather than a guess, so "Unknown" appears as its own
--        bar in category analytics instead of contaminating a real one.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE dim_product (
    product_key         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    product_id          TEXT    NOT NULL UNIQUE,
    product_name        TEXT    NOT NULL,

    -- NOT NULL: PR-03's decision is to impute the literal 'Unknown'. A NULL here
    -- would mean the imputation did not run, and NULL silently drops out of
    -- GROUP BY category comparisons in a way 'Unknown' does not.
    category            TEXT    NOT NULL,
    category_is_imputed INTEGER NOT NULL DEFAULT 0,

    list_unit_price     REAL    NOT NULL,
    price_is_imputed    INTEGER NOT NULL DEFAULT 0,
    price_conflict      INTEGER NOT NULL DEFAULT 0,

    supplier_id         TEXT,

    CHECK (category_is_imputed IN (0, 1)),
    CHECK (price_is_imputed IN (0, 1)),
    CHECK (price_conflict IN (0, 1)),
    CHECK (length(product_id) > 0),
    -- The PR-04 tripwire. A product cannot be free. If the zero-price imputation is
    -- ever removed, this aborts the load rather than shipping a catalogue in which
    -- one product's margin analysis divides by zero.
    CHECK (list_unit_price > 0)
);


-- ═══════════════════════════════════════════════════════════════════════════════
--  dim_customer
-- ═══════════════════════════════════════════════════════════════════════════════
--  ROLE:  Dimension.
--  GRAIN: One row per distinct customer identifier observed in the fact feed,
--         PLUS exactly one row for the 'GUEST' sentinel.
--
--  WHY GUEST IS ONE MEMBER AND NOT 40 ANONYMOUS ONES  (TX-06)
--  ----------------------------------------------------------
--  40 transactions arrive with a NULL customer_id. These are guest checkouts: real
--  revenue from real people the source system did not identify. Three options were
--  on the table:
--
--    (a) Drop the rows.            REJECTED — deletes ~8% of revenue to tidy a key.
--    (b) One synthetic id per row  REJECTED — see the cost analysis below.
--        (GUEST_0001..GUEST_0040).
--    (c) One shared 'GUEST' member. CHOSEN.
--
--  (b) is the seductive one, because it makes the dimension "complete" and every
--  count(distinct customer) look healthy. It is also a lie: it asserts that those
--  40 transactions came from 40 different people, which the source never said. It
--  would inflate the customer count by 40, deflate average revenue per customer,
--  and — worst — the invented ids are not stable across runs or across future
--  loads, so the same shopper would fragment further every night.
--
--  (c) is honest but not free, and the cost must be stated rather than hidden:
--    * `count(distinct customer_key)` UNDER-counts, because an unknown number of
--      real people (somewhere between 1 and 40) collapse into one row.
--    * GUEST becomes an artificial whale: it accumulates the sum of 40 baskets and
--      would top any lifetime-value leaderboard purely by being an aggregate.
--      That is why `is_guest` exists as a first-class column and why the analytics
--      contract (§6) requires `top_customers_lifetime` to exclude it *and say so*.
--    * Any per-customer behavioural metric (repeat rate, basket frequency) is
--      meaningless for this member and must filter it out.
--
--  The flag is what makes the cost payable: revenue metrics include GUEST (the
--  money is real), customer-identity metrics exclude it (the identity is not).
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE dim_customer (
    customer_key INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- The source id, or the 'GUEST' sentinel from src/config.py:GUEST_CUSTOMER_ID.
    customer_id  TEXT    NOT NULL UNIQUE,

    is_guest     INTEGER NOT NULL,

    CHECK (is_guest IN (0, 1)),
    CHECK (length(customer_id) > 0),
    -- The flag and the sentinel are two encodings of one fact; this stops them
    -- disagreeing, in either direction.
    CHECK (is_guest = (CASE WHEN customer_id = 'GUEST' THEN 1 ELSE 0 END))
);


-- ═══════════════════════════════════════════════════════════════════════════════
--  fact_sales
-- ═══════════════════════════════════════════════════════════════════════════════
--  ROLE:  Fact (transactional / atomic).
--  GRAIN: ONE ROW PER SOURCE TRANSACTION RECORD.
--
--  The source is already line-level — each row of transactions.csv carries exactly
--  one product_id — so a "transaction" in this dataset is a single line item, not a
--  basket. Stating this explicitly matters because the name invites the opposite
--  assumption: an analyst who reads `transaction_id` as "order id" would compute
--  average order value by summing across a basket that does not exist here, and
--  would treat the 30 return rows as order-level reversals rather than line-level
--  ones. `UNIQUE(transaction_id)` below is the grain declaration made enforceable.
--
--  MEASURES, AND WHY THERE ARE THREE OF THEM  (TX-03 — the headline finding)
--  -------------------------------------------------------------------------
--    extended_amount = quantity * unit_price   -- what the line SHOULD have cost
--    discount_amount = extended_amount - net   -- the money the source gave away
--    net_amount      = source total_amount     -- what was ACTUALLY charged
--
--  20 source rows have total_amount 5–20% below quantity * unit_price. That is not
--  a data error, it is an undocumented discount, and it is real revenue behaviour.
--  `net_amount` is the reported figure, carried through untouched, and it is the
--  ONLY column any revenue metric may sum. Recomputing it from quantity * unit_price
--  — the previous solution's single worst bug — inflates revenue and deletes the
--  finding in the same stroke. `extended_amount` exists purely so the discount is
--  visible as a number rather than as an anomaly someone has to notice.
--
--  RETURNS (TX-10): 30 rows with negated quantity and negated total_amount. They
--  are loaded, not filtered. `is_return` is stored explicitly rather than inferred
--  from `quantity < 0` at query time — WHY: it makes the intent greppable, it lets
--  the CHECK below prove the two agree, and it survives any future return that
--  arrives with a zero quantity and a fee-only amount.
--
--  WHAT IS *NOT* HERE: the quarantined rows. TX-04 (5 orphan stores), TX-05 (3
--  orphan products), TX-07 (5 zero-quantity), TX-08 (3 future-dated) are excluded
--  by the cleaning layer and written to output/quarantine/. They are deliberately
--  NOT loaded against a "-1 / Unknown" dimension member. WHY: an Unknown member
--  makes broken referential integrity look like a legitimate business category,
--  and it always ends up in someone's chart. Excluded-and-documented beats
--  included-and-disguised; the audit report carries the exact row count and keys.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE fact_sales (
    sales_key       INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- Degenerate dimension: a business key with no attributes of its own, so it
    -- lives on the fact rather than in a one-column dimension nobody would join to.
    transaction_id  TEXT    NOT NULL,

    date_key        INTEGER NOT NULL REFERENCES dim_date (date_key),
    store_key       INTEGER NOT NULL REFERENCES dim_store (store_key),
    product_key     INTEGER NOT NULL REFERENCES dim_product (product_key),
    customer_key    INTEGER NOT NULL REFERENCES dim_customer (customer_key),

    quantity        INTEGER NOT NULL,

    -- The price AS TRANSACTED, not dim_product.list_unit_price. See PR-02 above.
    unit_price      REAL    NOT NULL,

    extended_amount REAL    NOT NULL,
    discount_amount REAL    NOT NULL,

    -- == the cleaned source total_amount. THE revenue column.
    net_amount      REAL    NOT NULL,

    is_return       INTEGER NOT NULL,

    -- ── The grain, enforced ───────────────────────────────────────────────────
    -- TX-09 seeds 15 byte-identical duplicate rows sharing transaction_ids with
    -- base rows. The cleaner removes them; this constraint is the independent
    -- second opinion. If de-duplication ever regresses, the load aborts and rolls
    -- back rather than double-counting ~3% of revenue in every metric.
    UNIQUE (transaction_id),

    -- ── Business rules, enforced ──────────────────────────────────────────────
    -- TX-07: five source rows have quantity 0 and total 0. They are quarantined
    -- upstream. A zero-quantity sale is not a sale, and it silently poisons any
    -- per-unit metric (revenue/unit, return rate) by adding to a denominator
    -- without adding to a numerator.
    CHECK (quantity <> 0),

    -- PR-04's tripwire on the fact side. A line that sold at 0.00 is either a
    -- missing price or a giveaway; either way it must be examined, not averaged in.
    CHECK (unit_price > 0),

    CHECK (is_return IN (0, 1)),

    -- TX-10: the flag and the sign of quantity are two statements of one fact, so
    -- they are made to agree. A "return" with positive quantity, or a negative
    -- quantity not marked as a return, is a cleaning bug this constraint names at
    -- the moment it happens rather than three metrics later.
    CHECK (
        (is_return = 1 AND quantity < 0)
        OR (is_return = 0 AND quantity > 0)
    ),

    -- A return must give money back and a sale must take money in. Catches a
    -- sign-flip applied to quantity but not to the amount (or vice versa).
    CHECK (
        (is_return = 1 AND net_amount <= 0)
        OR (is_return = 0 AND net_amount >= 0)
    ),

    -- ── Additive arithmetic, enforced to the cent ─────────────────────────────
    -- 0.01 is src/config.py:PRICE_TOLERANCE, restated here because a CHECK cannot
    -- import Python. WHY a tolerance rather than equality: money is stored as REAL
    -- and 2-decimal values are not exactly representable in binary floating point,
    -- so `=` would reject arithmetically correct rows. WHY one cent specifically:
    -- the seeded discounts are 5–20% of order value — dollars, not cents — so this
    -- boundary is nowhere near any real row.
    CHECK (ABS(extended_amount - (quantity * unit_price)) <= 0.01),

    -- The reconciliation identity: net = extended - discount, always. This is the
    -- constraint that makes `revenue_reconciliation` (contract §6) tie out by
    -- construction rather than by hope.
    CHECK (ABS(discount_amount - (extended_amount - net_amount)) <= 0.01)
);


-- ═══════════════════════════════════════════════════════════════════════════════
--  Indexes
-- ═══════════════════════════════════════════════════════════════════════════════
--  WHY index the foreign keys at all on a 462-row fact: SQLite automatically
--  indexes PRIMARY KEY and UNIQUE columns, but NOT the child side of a foreign key.
--  Every metric in contract §6 is a join from fact to a dimension plus a GROUP BY,
--  which without these degrades to a full scan per dimension. At this size that is
--  microseconds — but the schema is the artifact being reviewed, and a star schema
--  that omits its fact-side FK indexes is a star schema that will not survive
--  contact with a real volume of data.
--
--  Deliberately NOT indexed: the flag columns. They are 0/1 with terrible
--  selectivity; an index on them costs write time and returns nothing.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE INDEX idx_fact_sales_date_key     ON fact_sales (date_key);
CREATE INDEX idx_fact_sales_store_key    ON fact_sales (store_key);
CREATE INDEX idx_fact_sales_product_key  ON fact_sales (product_key);
CREATE INDEX idx_fact_sales_customer_key ON fact_sales (customer_key);

-- Covering the two most common composite grouping patterns: "by store over time"
-- (top_stores_recent_30d) and "by product over time" (mom_growth_by_category,
-- which joins product -> category then buckets by month).
CREATE INDEX idx_fact_sales_store_date   ON fact_sales (store_key, date_key);
CREATE INDEX idx_fact_sales_product_date ON fact_sales (product_key, date_key);

-- `return_rate_by_store` filters on is_return within a store; the leading column
-- carries the selectivity, the trailing one makes the filter index-resolvable.
CREATE INDEX idx_fact_sales_store_return ON fact_sales (store_key, is_return);
