# Mindex Data Engineering Challenge — Retail Analytics Pipeline

Raw CSV → profiled → audited cleaning → SQLite star schema → six SQL metrics, with a runtime proof
that all 17 seeded data defects were found.

**This file is the canonical reviewer guide.** Every number in it is asserted against a live run by
`solution/scripts/check_readme_numbers.py`, which is itself run by the verification command below —
so if a figure here has gone stale, the build fails rather than the document lying quietly.

## Start here

| | |
|---|---|
| **The submission** | [`solution/`](solution/) — `src/`, `tests/`, `data/raw/`, `scripts/`, `output/` |
| **The dashboard** | [`dashboard/`](dashboard/) — Next.js evidence viewer over the same artifacts |
| **Verify all of it** | `python scripts/verify_submission.py` — one command, no arguments |

```bash
pip install -r requirements.txt
python scripts/verify_submission.py
```

That single command runs the full pipeline into a temporary directory, runs the
<!-- fig:test_count -->87<!-- /fig -->-test suite, re-checks every published figure in this document
and in `solution/README.md` against the artifacts it just produced, and then independently
re-derives the headline numbers from the raw CSVs and the warehouse using nothing but the standard
library. It prints a pass/fail table and exits non-zero if anything disagrees. Expect it to take
under a minute.

> **A note on the two `src/` directories.** `src/` and `tests/` **at the repository root are
> superseded** — they are this project's first attempt, kept for history, and every module in them
> raises on import with a pointer to the right path. The submitted implementation is
> `solution/src/` and `solution/tests/`. The reasoning is in
> [Repository layout](#repository-layout) at the end of this document.

## What the challenge asked, and what this does

The challenge supplies three deliberately dirty CSVs (`stores`, `products`, `transactions`) and asks
for a production-shaped pipeline: clean the data, load a dimensional model, and answer six business
questions. The interesting part is not the ETL — it is that several of the defects are **traps**,
where the obvious one-liner produces a clean-looking answer that is wrong.

This pipeline treats every defect as a *decision with a stated rationale* rather than a row to be
scrubbed. Nothing is dropped silently: every source row ends up either in the fact table or in a
quarantine file with a reason code, and the run **fails with exit code 1** if the defects it detects
do not match the catalog of what is provably in the data.

The three decisions that matter most:

- **TX-03** — 20 transactions have `total_amount` below `quantity × unit_price`. The reported total
  is preserved as authoritative. Recomputing it (the previous attempt's bug) would have overstated
  revenue by <!-- fig:discount_total -->$961.48<!-- /fig --> and erased the finding entirely.
- **PR-02** — `P005` appears twice with two different prices. That is a price change, not a
  duplicate. `dim_product` carries <!-- fig:p005_dim_price -->$150.11<!-- /fig --> as the current
  list price while `fact_sales` carries the transacted
  <!-- fig:p005_fact_price -->$141.61<!-- /fig -->.
- **ST-03** — two stores have a NULL region. The imputation draws only from the vocabulary already
  present in the column, so `dim_store` still holds exactly
  <!-- fig:distinct_regions -->4<!-- /fig --> regions and no invented fifth one.

Headline output: <!-- fig:clean_transactions -->474<!-- /fig --> transactions loaded,
<!-- fig:net_revenue -->$158,044.29<!-- /fig --> net revenue,
<!-- fig:defects_detected -->17<!-- /fig -->/<!-- fig:defect_classes -->17<!-- /fig --> defect
classes detected with <!-- fig:defect_mismatches -->0<!-- /fig --> count mismatches.

---

## Quickstart

From a fresh clone, at the repository root:

```bash
pip install -r requirements.txt          # pandas + numpy + pytest; everything else is stdlib
python scripts/verify_submission.py      # pipeline + tests + doc gate + independent assertions
```

To drive the stages by hand instead, work inside `solution/` — that is where `src` is importable
from:

```bash
cd solution
python -m src.pipeline                   # full run -> solution/output
python -m pytest -q                      # 87 tests
python scripts/check_readme_numbers.py --readme ../README.md   # assert THIS file against the run
python scripts/check_readme_numbers.py                         # and solution/README.md too
```

To write artifacts somewhere other than `solution/output`:

```bash
cd solution
python -m src.pipeline --output-dir /tmp/run
python scripts/check_readme_numbers.py --output-dir /tmp/run --readme ../README.md
```

The test suite also runs from the repository root without changing directory:

```bash
python -m pytest solution/tests -q       # 87 tests
```

Useful pipeline flags: `--as-of YYYY-MM-DD` (reference date), `--raw-dir DIR`,
`--skip-dashboard-export`.

**Pipeline exit codes:** `0` all 17 defect classes detected with expected counts · `1` ran but the
coverage proof failed · `2` raised an exception. The coverage gate is not advisory; a regression that
stops detecting TX-05 exits non-zero instead of quietly shipping a smaller fact table.

### Artifacts produced

| Path (relative to `solution/`) | What it is |
|---|---|
| `solution/output/warehouse.db` | SQLite star schema, the analytics source of truth |
| `solution/output/analytics.json` | Six metrics with title, description, SQL, `sql_ref`, `definition_note`, `column_units`, rows |
| `solution/output/audit_report.json` | Decision ledger: per-defect detected counts, actions, affected keys, coverage proof |
| `solution/output/profile_report.json` | Pre-cleaning profile — evidence captured *before* anything was changed |
| `solution/output/defect_catalog.json` | The 17 specs (detection, decision, rationale, source ref) |
| `solution/output/cleaned/*.csv` | Diffable post-cleaning snapshots |
| `solution/output/quarantine/*.csv` | Every excluded or flagged row, one file per (dataset, defect) |
| `solution/output/quarantine/transactions__lineage.csv` | One row per source transaction — the 505-row budget proof |
| `solution/output/dashboard_bundle.json` | Everything the Next.js dashboard reads, in one file |

---

## Reference date and window convention

Every time-relative calculation uses a single configurable `AS_OF_DATE =
<!-- fig:as_of_date -->2026-06-02<!-- /fig -->`, which is `seed_data.py`'s own "today".
`datetime.now()` is never called in pipeline logic — only to stamp `generated_at` metadata.

Two things break under wall-clock time: the trailing-window metrics go empty (the newest transaction
is already older than 30 real days), and the three future-dated TX-08 rows silently become ordinary
history as the calendar passes them. Pinning the date is what makes the run byte-reproducible.

**The 30-day window boundary, stated explicitly:** the window is
`[AS_OF_DATE − (RECENT_WINDOW_DAYS − 1), AS_OF_DATE]`, **inclusive at both ends** — that is
`[<!-- fig:window_start -->2026-05-04<!-- /fig -->, <!-- fig:as_of_date -->2026-06-02<!-- /fig -->]`,
spanning <!-- fig:window_days -->30<!-- /fig --> calendar days and covering
<!-- fig:window_rows -->181<!-- /fig --> fact rows. The start date is *derived* from `AS_OF_DATE` in
`RunConfig.recent_window_start` (`solution/src/config.py`), never hardcoded. This convention is load-bearing: reading it as
`[AS_OF − 30, AS_OF]` reorders the published #1 and #2 stores.

Transaction data spans <!-- fig:first_date -->2026-03-05<!-- /fig --> to
<!-- fig:last_date -->2026-06-01<!-- /fig -->.

Other pinned settings: return-rate alert at <!-- fig:return_threshold_pct -->10%<!-- /fig -->, TX-03
reconciliation tolerance <!-- fig:price_tolerance -->$0.01<!-- /fig -->, and a
<!-- fig:date_format_count -->3<!-- /fig -->-rung ordered date-format ladder for TX-01.

---

## Data-quality decision matrix

All 17 seeded defect classes. `Count` is the number of **source rows observed**, taken live from
`audit_report.json` — not post-filter survivors, so the completeness proof compares like with like.

### stores.csv — <!-- fig:raw_stores -->16<!-- /fig --> rows → <!-- fig:clean_stores -->15<!-- /fig -->

| Code | What it is | Count | Decision | Why |
|---|---|---|---|---|
| **ST-01** | `S003` ZIP is `0938` — four chars, leading zero eaten upstream | <!-- fig:det_ST-01 -->1<!-- /fig --> | Left-pad *only rows failing the 5-digit test* → <!-- fig:s003_zip -->00938<!-- /fig -->, set `zip_is_suspect` = <!-- fig:s003_zip_suspect -->1<!-- /fig --> | `00938` is structurally valid but is **not** a real NY ZIP (006xx–009xx is Puerto Rico; Greece, NY is 14xxx). Padding restores a well-formed field, not a true one, so the row is flagged for human verification rather than "corrected". A blanket `zfill(5)` would hide which row was ever wrong and would corrupt any ZIP+4. |
| **ST-02** | `S007` appears twice: "Downtown Rochester" vs "Rochester Downtown", all other attributes identical | <!-- fig:det_ST-02 -->1<!-- /fig --> | Three-stage ranked survivorship rule (below); elects "Downtown Rochester" | Two rows describe one real store, so collapsing is right — but *how* must be policy, not row order. `keep="first"` gives a different winner if the extract is re-sorted, silently changing the warehouse with no code change. |
| **ST-03** | NULL region on `S013`, `S014` (both Portland, OR) | <!-- fig:det_ST-03 -->2<!-- /fig --> | Impute `West` from a state→region map built at runtime from the column's own values; `region_is_imputed = 1` on <!-- fig:regions_imputed -->2<!-- /fig --> rows | Detailed below. |

**ST-02's survivorship rule is three stages, applied in order** — it is not a lexicographic
tie-breaker:

1. **Fewest nulls.** Prefer the more complete record.
2. **Earliest `opened_date`.** Prefer the record that has been on file longest.
3. **Lexicographically first `store_name`.** Deterministic last resort only.

Here stages 1 and 2 tie (the rows differ only in `store_name`), so stage 3 decides and elects
"Downtown Rochester". The losing variant and the reason it lost are both written to the audit ledger.
This must be resolved *before* the dimension loads, because `dim_store.store_id` is `UNIQUE` — an
unresolved duplicate is not a bad number, it is a failed load.

### products.csv — <!-- fig:raw_products -->32<!-- /fig --> rows → <!-- fig:clean_products -->30<!-- /fig -->

| Code | What it is | Count | Decision | Why |
|---|---|---|---|---|
| **PR-01** | `P012` byte-identical second row | <!-- fig:det_PR-01 -->1<!-- /fig --> | Drop the second copy | Every attribute agrees, so nothing is lost. The subtlety is **ordering**: full-row de-duplication must run *before* key-level de-duplication, or a single `drop_duplicates(subset=["product_id"])` sweeps up P012 and P005 together and reports both as harmless — which is exactly how PR-02 vanished from the previous attempt. |
| **PR-02** | `P005` appears twice with two different prices (+<!-- fig:p005_price_delta -->$8.50<!-- /fig -->) | <!-- fig:det_PR-02 -->1<!-- /fig --> | Treat as a slowly-changing attribute, not a duplicate. Both versions quarantined as evidence; `dim_product` elects MAX | Detailed below. |
| **PR-03** | NULL category on `P003`, `P009`, `P016`, `P023`, `P029` | <!-- fig:det_PR-03 -->5<!-- /fig --> | Impute literal `"Unknown"`; `category_is_imputed = 1` on <!-- fig:categories_imputed -->5<!-- /fig --> rows | Nothing in the file supports inferring a category: names are synthetic and `supplier_id` cycles across all five categories by construction, so it carries zero signal. `"Unknown"` beats NULL because a named bucket shows up in every category chart as a visible gap someone will fix, whereas NULLs are dropped by most `GROUP BY`s and the revenue evaporates. Dropping the products was never an option — they carry real transactions. |
| **PR-04** | `P027` `unit_price = 0.00` in the product master | <!-- fig:det_PR-04 -->1<!-- /fig --> | Read `0.00` as *missing*: impute category median <!-- fig:p027_dim_price -->$126.96<!-- /fig -->, `price_is_imputed = 1`, quarantine as evidence. Fact rows keep the transacted <!-- fig:p027_fact_price -->$195.34<!-- /fig --> | A retailer does not stock a $0.00 item. Left unflagged, a downstream analyst computes a 100% discount on every P027 line and believes it. The transacted price is *evidence* recorded in the audit note, deliberately **not** written into the dimension — cross-populating master data from a fact table is a data steward's call, not an ETL job's. |

### transactions.csv — <!-- fig:raw_transactions -->505<!-- /fig --> rows → <!-- fig:clean_transactions -->474<!-- /fig -->

| Code | What it is | Count | Decision | Why |
|---|---|---|---|---|
| **TX-01** | Three date formats in one column (ISO, `MM/DD/YYYY`, `DD-MM-YYYY`) | <!-- fig:det_TX-01 -->20<!-- /fig --> | Parse against an **ordered** ladder of explicit formats; assert zero rows left unparsed | The quietest catastrophe here. A single `pd.to_datetime(errors="coerce")` either NaTs 20 real transactions or, worse, picks one day-first/month-first guess for the whole column and *misparses* them — output that looks complete and is wrong. The ladder works because the formats are separator-disjoint: `/` marks US, a four-digit head marks ISO, a two-digit head with `-` marks EU. All 20 recover to the correct calendar date. |
| **TX-02** | `total_amount` as currency strings (`"$142.50"`) | <!-- fig:det_TX-02 -->25<!-- /fig --> | Strip symbol/separators/whitespace, honour parenthesised negatives, cast to float; anything still unparseable is quarantined, never zeroed | CSVs are read with `dtype=str` precisely so this survives. Let pandas infer and the column becomes 25 strings mixed with 480 floats. The `errors="coerce".fillna(0)` shortcut is the dangerous one: it understates revenue by roughly $3.5k while producing a beautifully clean column no test would flag. |
| **TX-03** | Reported total is 5–20% below `quantity × unit_price` | <!-- fig:det_TX-03 -->20<!-- /fig --> | **Preserve `total_amount` verbatim.** Add `extended_amount`, expose `discount_amount`, set `has_discount` | Detailed below. |
| **TX-04** | Orphan `store_id` (`S016`–`S019`) | <!-- fig:det_TX-04 -->5<!-- /fig --> | Quarantine; exclude from `fact_sales`; state the withheld revenue | Silently dropping loses real revenue with no trace. Routing to an "Unknown Store" member keeps the money but pollutes every store metric with a bucket nobody can act on. Quarantining is the honest middle: store analytics stay clean, `PRAGMA foreign_keys = ON` stays genuinely enforceable, and the rows sit on disk counted and priced. Four sequential IDs immediately after the last real store is the signature of a stale dimension extract, not corrupt transactions. |
| **TX-05** | Orphan `product_id` (`P031`, `P032`) | <!-- fig:det_TX-05 -->3<!-- /fig --> | Identical treatment to TX-04 | Deliberately the same policy: two structurally identical problems given two different treatments is what a reviewer will rightly attack. These rows are individually the most valuable orphans in the file, so dropping them quietly would move revenue more than the row count suggests. |
| **TX-06** | NULL `customer_id` — guest checkouts | <!-- fig:det_TX-06 -->40<!-- /fig --> | **Keep every row.** `customer_id = "GUEST"`, `is_guest = True`. <!-- fig:guest_rows -->40<!-- /fig --> fact rows worth <!-- fig:guest_revenue -->$12,164.03<!-- /fig --> | Not corruption — a legitimate retail event the schema has no flag for. Dropping deletes ~8% of real revenue and skews every store, category and regional figure downward in a way no reconciliation catches. NULL cannot stay because `dim_customer` needs a natural key and `fact_sales` a non-null FK. GUEST is excluded from the customer leaderboard only, because it is 40 unrelated shoppers fused into one pseudo-person that would top the ranking by construction. |
| **TX-07** | `quantity = 0` and `total_amount = 0` | <!-- fig:det_TX-07 -->5<!-- /fig --> | Quarantine; also excluded from the TX-03 check | A sale of zero units for zero dollars is a voided line. It adds nothing to revenue but is not harmless: left in the fact table it inflates the transaction count AOV divides by, pushing AOV down ~1% for no economic reason. Removing them changes no total and repairs a denominator. |
| **TX-08** | Dated after `AS_OF_DATE` (+8, +16, +25 days) | <!-- fig:det_TX-08 -->3<!-- /fig --> | Quarantine, compared against `AS_OF_DATE` | Admitting them leaks revenue into a period that has not closed. The check runs **after** date parsing on purpose: the previous attempt's 20 coerce-mangled TX-01 rows got reported here, turning a parser bug into a fictitious business finding. |
| **TX-09** | 15 byte-identical duplicate rows, same `transaction_id` | <!-- fig:det_TX-09 -->15<!-- /fig --> | Drop copies, keep first. De-duplicate on the **whole row**, not the key | Identical IDs with identical measures means a re-extract, not two sales; leaving them double-counts ~3% of revenue, concentrated on specific stores because they cluster in one ID range. Keying on the full row means a future file where one ID carries two *different* amounts surfaces as a conflict instead of collapsing to whichever row sorted first. `UNIQUE(transaction_id)` on the fact table is the independent second opinion. |
| **TX-10** | Returns: new IDs, negated quantity and amount | <!-- fig:det_TX-10 -->30<!-- /fig --> | Preserve with negative measures intact, `is_return = True`. <!-- fig:return_rows -->30<!-- /fig --> rows, <!-- fig:return_units -->88<!-- /fig --> units | Returns are the credit side of the ledger. Dropping them overstates net revenue by their full value; `abs()` overstates by twice that and converts a refund into a sale — the worst possible sign error. Keeping the sign is what makes `SUM(net_amount)` genuinely *net*, and it is the only reason a return rate is computable at all. |

Total source rows touched by at least one defect: <!-- fig:rows_affected -->178<!-- /fig -->.

---

### Judgement call 1 — TX-03: preserving reported totals

20 rows report a `total_amount` 5–20% below `quantity × unit_price`. The money really moved at the
discounted price, so `total_amount` is a **fact** and `quantity × unit_price` is a **derivation**.
"Fixing" the fact to agree with the derivation inverts the direction of truth.

`total_amount` is never recomputed anywhere in `solution/src/` — the only textual matches are docstrings
explaining why not. Instead the fact table carries three measures, so the discrepancy is *visible*:

```
extended_amount = quantity * unit_price   -- what the line should have cost
discount_amount = extended_amount - net   -- the money the source gave away
net_amount      = source total_amount     -- what was actually charged  (authoritative)
```

**What recomputing would have cost.** <!-- fig:discount_rows -->20<!-- /fig --> fact rows carry a
non-zero discount, totalling <!-- fig:discount_total -->$961.48<!-- /fig -->. Setting
`total_amount = quantity × unit_price` would have published `$158,044.29 + $961.48 = $159,005.77` —
an overstatement of <!-- fig:discount_share_pct -->0.61%<!-- /fig --> of net revenue.

That percentage is small, and that is exactly the point: it is small enough to pass any smell test,
and it would have deleted the only evidence that an unmodelled promotion or manual override is
flowing through a schema with nowhere to record it. The finding, not the dollars, is what would have
been lost.

The reconciliation metric exists to make the chain checkable by hand rather than asserted:

| Line | Value |
|---|---|
| Gross list value (`Σ quantity × unit_price`, sales only) | <!-- fig:gross_list_value -->$168,957.80<!-- /fig --> |
| − Discounts (TX-03) | <!-- fig:discount_total -->$961.48<!-- /fig --> |
| = Gross sales net of discount | <!-- fig:gross_net_of_discount -->$167,996.32<!-- /fig --> |
| + Returns (TX-10) | <!-- fig:returns_value -->-$9,952.03<!-- /fig --> |
| **= Net revenue** | **<!-- fig:net_revenue -->$158,044.29<!-- /fig -->** |
| Line-level delta | <!-- fig:line_level_delta -->$0.00<!-- /fig --> |
| Aggregate delta | <!-- fig:aggregate_delta -->$0.00<!-- /fig --> |

Both deltas are published because they are derived by different routes. `line_level_delta` is
`SUM(extended_amount − discount_amount − net_amount)` over non-returns, computed from raw column
values, so it fires as soon as any single row's money columns stop agreeing with one another.
`aggregate_delta` recomputes the identity from the **rounded** figures printed above it, so the
published table checks itself, rounding included. If the 20 discounts had been recomputed away,
`discount_total` would read $0.00 — this metric is the only place that absence would be visible.

Stated honestly: neither delta can detect a *uniform rescaling* of all money columns together,
because that stays internally consistent. That case is caught elsewhere, by
`loader.verify_warehouse()` check 4, which ties `SUM(fact_sales.net_amount)` back to the cleaned
CSV's `SUM(total_amount)` at half-a-cent tolerance (<!-- fig:tie_out_cents -->0<!-- /fig --> cents of
drift on this run) and aborts the load on failure.

### Judgement call 2 — PR-02: a price change wearing a duplicate's clothes

`P005` appears twice in `products.csv` with `unit_price` differing by exactly
<!-- fig:p005_price_delta -->$8.50<!-- /fig -->. This is the trap in that file:
`drop_duplicates(subset=["product_id"])` removes the row and the finding in the same instruction,
and because the source shuffle puts the higher price first, *which* price survives is a coin flip.

| Table | Column | Value | Meaning |
|---|---|---|---|
| `dim_product` | `list_unit_price` | <!-- fig:p005_dim_price -->$150.11<!-- /fig --> | Current list price — a **reference attribute** |
| `fact_sales` | `unit_price` | <!-- fig:p005_fact_price -->$141.61<!-- /fig --> | Price **as transacted**, on all <!-- fig:p005_fact_rows -->19<!-- /fig --> kept P005 rows |

The dimension elects the higher price by an explicit `MAX` policy, on the stated assumption that the
appended record is the newer extract — an assumption written into the audit note so it can be
contradicted. `MAX` is chosen over "last row wins" specifically because it is *order-independent*: a
re-sorted extract cannot change the warehouse.

Why this is safe rather than merely arbitrary: **revenue never reads the dimension.**
`fact_sales.unit_price` comes from `transactions.csv`, so electing either price cannot move a single
revenue figure. All 20 source P005 transactions rang at $141.61 and none at $150.11, which means the
increase post-dates the transaction window — precisely why the fact must carry the transacted price.
Failing to *report* the conflict, though, means the business never learns its product master holds
two truths. The correct long-term fix is a Type-2 dimension with effective dates, which this source
cannot support because it carries no date on the price.

The same separation applies to PR-04: `dim_product.P027` =
<!-- fig:p027_dim_price -->$126.96<!-- /fig --> (imputed category median) while its fact rows carry
<!-- fig:p027_fact_price -->$195.34<!-- /fig -->. No fact data is laundered into master data.

An unguarded `MAX`→`MIN` flip would silently reverse this finding while the coverage table still
printed "PR-02 · Expected 1 · Detected 1 · OK", so `products.py` asserts at runtime that the elected
price *is* the observed maximum, and the pipeline aborts if it is not.

### Judgement call 3 — ST-03: imputing only from observed vocabulary

`S013` and `S014` (both Portland, OR) have no region. The state→region map is **built at runtime
from the values already present in the region column**, never from a hardcoded dictionary. OR
resolves to `West` because every other Pacific/Mountain store in the file (AZ, WA) is already
labelled `West`.

This is a direct response to the previous attempt's failure: it hardcoded `NY → "East"` while the
data's own vocabulary says `"Northeast"`. That invented a sixth region which split the Northeast in
two and quietly corrupted average order value by region — a wrong answer that looked completely
reasonable on a chart. Deriving the map from observed values makes that entire class of bug
impossible to reintroduce.

`dim_store` holds exactly <!-- fig:distinct_regions -->4<!-- /fig --> distinct regions and
`aov_by_region` returns 4 rows. `region_is_imputed = 1` on the
<!-- fig:regions_imputed -->2<!-- /fig --> affected rows, so anyone needing source-only figures can
exclude them. Leaving the region NULL was the alternative, rejected because two stores would then
vanish from every regional roll-up and understate the West.

---

## Row-budget reconciliation

Every one of the <!-- fig:lineage_total -->505<!-- /fig --> source transaction rows is accounted for.
`solution/output/quarantine/transactions__lineage.csv` carries **one row per source row** with its
`source_row` ordinal, disposition, reason code and reported amount — so the budget is a file a
reviewer can `GROUP BY`, not a claim.

| Disposition | Reason | Rows |
|---|---|---|
| kept | — | <!-- fig:lineage_kept -->474<!-- /fig --> |
| quarantined | TX-04 orphan store | <!-- fig:lineage_tx04 -->5<!-- /fig --> |
| quarantined | TX-05 orphan product | <!-- fig:lineage_tx05 -->3<!-- /fig --> |
| quarantined | TX-07 zero quantity | <!-- fig:lineage_tx07 -->5<!-- /fig --> |
| quarantined | TX-08 future date | <!-- fig:lineage_tx08 -->3<!-- /fig --> |
| dropped | TX-09 exact duplicate | <!-- fig:lineage_tx09 -->15<!-- /fig --> |
| **Total** | | **<!-- fig:lineage_total -->505<!-- /fig -->** |

That is <!-- fig:lineage_kept -->474<!-- /fig --> kept +
<!-- fig:lineage_quarantined -->16<!-- /fig --> quarantined +
<!-- fig:lineage_dropped -->15<!-- /fig --> dropped = <!-- fig:lineage_total -->505<!-- /fig -->.
The five reason sets are mutually disjoint, so there is no double counting.

Dimensions: stores <!-- fig:raw_stores -->16<!-- /fig --> →
<!-- fig:clean_stores -->15<!-- /fig --> (one ST-02 loser); products
<!-- fig:raw_products -->32<!-- /fig --> → <!-- fig:clean_products -->30<!-- /fig --> (one PR-01
duplicate, one PR-02 loser).

**Quarantine files hold two different kinds of row**, and conflating them is why an earlier reader
computed 32 − 4 = 28 ≠ 30. Every quarantine CSV therefore carries a `disposition` column:

- `dropped` — the row is **not** in the cleaned output; it counts against the row budget.
- `evidence` — the business key **survives**; this copy is a review snapshot of a decision made about
  it (S003's padded ZIP, P027's imputed price, the P005 row that won).

<!-- fig:quarantine_csv_rows -->38<!-- /fig --> rows sit across all quarantine CSVs; only the
`dropped` ones reduce a row count. Sum the `dropped` rows and the budget reconciles exactly.

---

## Warehouse schema

SQLite star schema at `solution/output/warehouse.db`. One fact, four conformed dimensions. Loaded dims → fact
inside a **single transaction** with `PRAGMA foreign_keys = ON`; on any failure the whole load rolls
back, so a partial database is never produced.

```
dim_date ────┐
dim_store ───┼──> fact_sales
dim_product ─┤
dim_customer ┘
```

| Table | Grain — stated explicitly | Rows |
|---|---|---|
| `dim_date` | One row per **calendar day** in the loaded range — dense, including days with no sales | <!-- fig:dim_date_rows -->90<!-- /fig --> |
| `dim_store` | One row per **surviving physical store** | <!-- fig:dim_store_rows -->15<!-- /fig --> |
| `dim_product` | One row per **product**, holding its current list price | <!-- fig:dim_product_rows -->30<!-- /fig --> |
| `dim_customer` | One row per **distinct customer identifier observed in the fact feed**, including the `GUEST` sentinel | <!-- fig:dim_customer_rows -->229<!-- /fig --> (<!-- fig:real_customers -->228<!-- /fig --> real + GUEST) |
| `fact_sales` | **One row per source transaction record** | <!-- fig:fact_rows -->474<!-- /fig --> |

The fact grain is worth labouring because the name invites the opposite assumption. The source is
already line-level — each row of `transactions.csv` carries exactly one `product_id` — so a
"transaction" here is a **single line item, not a basket**. An analyst reading `transaction_id` as
"order id" would compute average order value across a basket that does not exist, and would treat the
30 returns as order-level reversals rather than line-level ones. `UNIQUE(transaction_id)` is that
grain declaration made enforceable.

### Why surrogate keys

Every dimension has a meaningless auto-increment `*_key` primary key and keeps the source business
key as a `UNIQUE NOT NULL` natural key. Three reasons, all demonstrated by this dataset:

1. **Source keys are not trustworthy.** `store_id` arrives duplicated (ST-02) and five transactions
   reference stores that do not exist (TX-04). A source that emits duplicate PKs and dangling
   references will re-key again. Binding facts to `store_id` means a future re-key rewrites the whole
   fact table; binding to `store_key` rewrites one dimension row.
2. **SCD Type 2 is already implied.** PR-02 proves `dim_product` has slowly-changing attributes. The
   day the business asks "what was the list price when this sold?", `dim_product` needs two rows for
   P005 — expressible only if the primary key is not `product_id`. The surrogate key makes that
   change additive rather than a migration.
3. **Narrower fact rows, integer joins.** At 505 rows this is aesthetic; the habit is what matters.

Trade-off, stated: surrogate keys make the raw fact table unreadable without joins and add a
key-resolution step that can fail. `loader.py` turns that into a feature by asserting that **zero**
natural keys fail to resolve, so a cleaning-layer escape becomes a loud crash rather than a silently
dropped row.

### CHECK constraints are load-bearing, not decorative

An adversarial audit attempted to sabotage the warehouse directly. Three attempts were rejected
outright by these constraints:

```sql
CHECK (ABS(extended_amount - (quantity * unit_price)) <= 0.01)
CHECK (ABS(discount_amount - (extended_amount - net_amount)) <= 0.01)
CHECK ((is_return = 1 AND quantity < 0) OR (is_return = 0 AND quantity > 0))
```

Also on the same table: `UNIQUE(transaction_id)` (the TX-09 second opinion — a de-duplication
regression aborts the load instead of double-counting ~3% of revenue), `CHECK (quantity <> 0)`
(TX-07), `CHECK (unit_price > 0)` (PR-04's tripwire on the fact side), and a sign-agreement check
between `is_return` and `net_amount`. `dim_date` constrains `date_key` to equal
`strftime('%Y%m%d', full_date)` so the smart key cannot drift from the date it encodes;
`dim_customer` constrains `is_guest` to equal `customer_id = 'GUEST'` so the flag and the sentinel
cannot disagree.

Verification on this run: `PRAGMA foreign_key_check` returns
<!-- fig:fk_violations -->0<!-- /fig --> violations, FK enforcement is proven by a deliberately
rejected probe insert, and the revenue tie-out differs by
<!-- fig:tie_out_cents -->0<!-- /fig --> cents.

Money is stored as `REAL` rather than integer cents. That is a deliberate compromise: the source is
2-decimal text, the analytics must reproduce reported totals exactly, and every comparison in the
project is tolerance-based to the cent anyway — with the tolerance stated in `solution/src/config.py` and
applied in the CHECKs rather than assumed.

---

## Metric definitions

All six live in `solution/src/analytics/queries.py` as named SQL constants, execute against the warehouse (not
against DataFrames), and serialise with `title`, `description`, `sql`, `sql_ref`, `definition_note`
and `column_units`. Every ratio is float-forced and every denominator is `NULLIF`-guarded.

### `top_stores_recent_30d`

**Numerator:** `SUM(net_amount)` over all transactions — sales **and** returns.
**Population:** fact rows whose `full_date` falls in
`[<!-- fig:window_start -->2026-05-04<!-- /fig -->, <!-- fig:as_of_date -->2026-06-02<!-- /fig -->]`,
inclusive at both ends. Returns reduce revenue by their signed amount; they are not excluded.

| # | Store | Region | Net revenue | Txns |
|---|---|---|---|---|
| 1 | <!-- fig:store1_name -->Galleria at Crystal Run<!-- /fig --> | <!-- fig:store1_region -->Northeast<!-- /fig --> | <!-- fig:store1_revenue -->$6,770.08<!-- /fig --> | <!-- fig:store1_txns -->17<!-- /fig --> |
| 2 | <!-- fig:store2_name -->Southpark Meadows<!-- /fig --> | South | <!-- fig:store2_revenue -->$6,555.48<!-- /fig --> | |
| 3 | <!-- fig:store3_name -->Eastview Mall<!-- /fig --> | Northeast | <!-- fig:store3_revenue -->$5,865.13<!-- /fig --> | |
| 4 | <!-- fig:store4_name -->Lloyd Center<!-- /fig --> | West | <!-- fig:store4_revenue -->$4,979.12<!-- /fig --> | |
| 5 | <!-- fig:store5_name -->The Domain<!-- /fig --> | South | <!-- fig:store5_revenue -->$4,938.18<!-- /fig --> | |

### `mom_growth_by_category`

**Formula:** `(monthly_revenue − LAG(monthly_revenue)) / LAG(monthly_revenue) × 100`, partitioned by
category and ordered by `year_month`. A category's first month yields `NULL` (no prior month), and
the `NULLIF` guard prevents division by zero. <!-- fig:mom_rows -->22<!-- /fig --> rows: six
categories across four months, with two legitimate absences — Food & Beverage and Office Supplies
genuinely had zero June transactions, verified against the fact table. Nothing was dropped.

**Partial-month caveat — read this before quoting a number from here.** The first and last months of
the extract are not full months. 2026-03 has <!-- fig:days_march -->27<!-- /fig --> transacting days;
2026-06 has exactly <!-- fig:days_june -->1<!-- /fig --> (2026-06-01, the day before `AS_OF_DATE`).
That single day is what produces the −98% figures: Apparel reads
<!-- fig:apparel_june_pct -->-95.92%<!-- /fig --> for June off
<!-- fig:apparel_june_days -->1<!-- /fig --> day of data, and Food & Beverage reads
<!-- fig:fb_april_pct -->+403.23%<!-- /fig --> for April off a thin March base. Neither is a business
signal; both are artefacts of where the extract was cut.

The metric therefore emits a `days_with_data` column per row so the asymmetry is visible rather than
implied, and the `definition_note` states the caveat. **Only 2026-04 and 2026-05 are complete
months**, which is what makes Apparel's <!-- fig:apparel_may_pct -->29.66%<!-- /fig --> May reading
the kind of number that can actually be quoted.

### `return_rate_by_store`

Two rates are published for all <!-- fig:stores_scored -->15<!-- /fig --> stores, because the two
denominators give materially different answers and picking one silently is how two teams end up
quoting different numbers from the same warehouse:

- **Transaction-based:** `return transactions / total transactions`
- **Unit-based:** `returned units / (sold units + returned units)` ← the `>10%` flag uses this one

| Store | Txn rate | Unit rate | Flagged |
|---|---|---|---|
| S006 Lakeside Shopping Ctr | <!-- fig:s006_txn_rate -->12.50%<!-- /fig --> | <!-- fig:s006_unit_rate -->13.73%<!-- /fig --> | yes |
| S015 Alderwood Mall | <!-- fig:s015_txn_rate -->15.38%<!-- /fig --> | <!-- fig:s015_unit_rate -->13.51%<!-- /fig --> | yes |
| S008 Galleria at Crystal Run | <!-- fig:s008_txn_rate -->11.63%<!-- /fig --> | <!-- fig:s008_unit_rate -->10.48%<!-- /fig --> | yes |

**Documented deviation from the contract.** The challenge specifies the unit rate as
`SUM(returned units) / SUM(sold units)`. This implementation uses `returned / (sold + returned)`
instead. Reason: the contracted form is a returns-to-sales *ratio*, which is unbounded and can exceed
100% when a period's returns settle against an earlier period's sales — a figure that reads as an
error to anyone seeing it on a dashboard. The denominator used here is total units moved, so the
result is a genuine proportion bounded at 100%, answering "what share of everything that crossed the
counter came back?".

The deviation is material but not decisive, and both forms are recomputable from the published
columns: S006 moved <!-- fig:s006_units_sold -->88<!-- /fig --> units out and took
<!-- fig:s006_units_returned -->14<!-- /fig --> back, so it reads
<!-- fig:s006_unit_rate -->13.73%<!-- /fig --> here versus
<!-- fig:s006_contract_rate -->15.91%<!-- /fig --> under the contracted denominator. **The set of
stores breaching the <!-- fig:return_threshold_pct -->10%<!-- /fig --> threshold is identical either
way**, so no flag, alert or ranking changes. A silent deviation from a stated interface would be a
defect; a documented and reasoned one is a decision.

### `aov_by_region`

**Numerator:** `SUM(net_amount)` over **non-return** transactions. **Denominator:** the count of
those same transactions. Returns are excluded per the challenge spec — including them would mix a
refund into an "average order".

| Region | Txns | AOV |
|---|---|---|
| Northeast | <!-- fig:aov_northeast_txns -->165<!-- /fig --> | <!-- fig:aov_northeast -->$389.05<!-- /fig --> |
| South | <!-- fig:aov_south_txns -->83<!-- /fig --> | <!-- fig:aov_south -->$384.49<!-- /fig --> |
| Midwest | <!-- fig:aov_midwest_txns -->46<!-- /fig --> | <!-- fig:aov_midwest -->$375.82<!-- /fig --> |
| West | <!-- fig:aov_west_txns -->150<!-- /fig --> | <!-- fig:aov_west -->$364.02<!-- /fig --> |

Four regions, not five — see ST-03 above.

### `top_customers_lifetime`

**Numerator:** `SUM(net_amount)` per customer across all time. **Population:** `WHERE is_guest = 0`.
The GUEST exclusion is stated in the metric's `definition_note`, not buried in a `WHERE` clause,
because it is a modelling decision a reader is entitled to disagree with.

| # | Customer | Lifetime spend | Txns |
|---|---|---|---|
| 1 | <!-- fig:cust1_id -->CUST0213<!-- /fig --> | <!-- fig:cust1_spend -->$3,077.96<!-- /fig --> | <!-- fig:cust1_txns -->4<!-- /fig --> |
| 2 | <!-- fig:cust2_id -->CUST0170<!-- /fig --> | <!-- fig:cust2_spend -->$2,854.52<!-- /fig --> | |
| 3 | <!-- fig:cust3_id -->CUST0287<!-- /fig --> | <!-- fig:cust3_spend -->$2,825.70<!-- /fig --> | |

### `revenue_reconciliation`

Covered in full under TX-03 above.

---

## Testing

<!-- fig:test_count -->87<!-- /fig --> tests, all passing. Run with `python -m pytest solution/tests -q` from the repository root, or
`python -m pytest -q` from inside `solution/`.

| Module | What it defends |
|---|---|
| `test_cleaning.py` | Per-defect cleaning behaviour: date ladder, currency parsing, discount preservation, survivorship, imputation, guest sentinel, returns |
| `test_analytics.py` | Metric SQL against a synthetic warehouse — sign handling, guest exclusion, threshold direction |
| `test_metric_contracts.py` | The metric registry as an interface: required ids, `column_units` completeness, window-boundary arithmetic, and a **falsifiable** reconciliation delta |
| `test_defect_gaps.py` | The codes the previous suite never asserted (TX-05, TX-09, PR-04) plus the untested robustness guards |
| `test_golden_end_to_end.py` | The real `solution/data/raw/` CSVs through the real pipeline with pinned numbers: 474 rows, $158,044.29, the exact top-5 ranking, 17/17 coverage |
| `test_profiler.py` | Profiling primitives and null/type detection |

### Mutation testing — including what it found wrong

The suite was mutation-tested with 18 deliberate sabotages applied to scratch copies of the tree. A
mutation is "killed" if `pytest` exits non-zero or the pipeline aborts. **Five of the 18 initially
survived.** That is reported here because a suite's honest measure is what it fails to catch, and
because the fix for each is more informative than the original pass rate.

| Mutation | Then | What was done | Now |
|---|---|---|---|
| **M2** — PR-02 elects `MIN` instead of `MAX` for P005's list price | Survived, and the coverage table still printed "PR-02 · Detected 1 · OK". The most dangerous of the five: the completeness proof gave false assurance | Runtime assertion in `products.py` that the elected price *is* the observed maximum, plus unit and golden-file tests pinning `dim_product.P005 = $150.11` and the +$8.50 delta | Killed by the pipeline (exit 2) **and** by tests |
| **M6** — `reconciliation_delta` replaced with the literal `0.0` | Survived. The original delta was `SUM(net WHERE ret=0) + SUM(net WHERE ret=1) − SUM(net)` — algebraically zero for *any* data. It reported `0.00` with $79k of fabricated revenue sitting in the warehouse | Replaced with two independent deltas (line-level and aggregate), and a test that **injects a known $5.00 arithmetic error and asserts the delta reports exactly that amount** — so the test itself cannot be a tautology | Killed by tests |
| **M5** — `RECENT_WINDOW_DAYS` 30 → 60 | Survived; silently reordered the published top-5 | The window boundary asserted directly, plus golden-file assertions on window length, the row population it sees, and the exact ranking | Killed by tests (9 failures) |
| **M18** — the 30-day window loses its upper bound | Survived; invisible on the shipped data, which is why it needed a synthetic fixture with rows on both sides of the boundary | A test placing rows immediately outside each end of the window and asserting both are excluded | Killed by tests |
| **M12** — ST-01's digit guard replaced with unconditional `zfill(5)` | Survived. Behaviourally inert on this dataset (every other ZIP is already 5 chars) — a latent robustness gap, but verbatim the previous solution's named bug #5 | A fixture with a non-paddable ZIP (`"N/A"`) asserting it is left untouched rather than becoming `"00N/A"` | Killed by tests |

All five were re-applied to the current tree while this README was written, and every one now fails
the build. Two other mutations (M9, disabling TX-09 de-duplication; M10, disabling the TX-05 orphan
check) were never caught by tests and are still stopped cold by the **warehouse itself** — the
`UNIQUE` constraint and the loader's refusal to resolve an unknown natural key. That is what a
well-designed schema is for.

### Independent verification

An adversarial audit independently recomputed all six metrics from the raw CSVs in pandas — before
reading `queries.py` — and reproduced every figure to the cent. It also confirmed: all 20 TX-01 dates
parse to the *correct* calendar date, including both genuinely ambiguous strings; the 505-row budget
conserves exactly, with its own disposition logic agreeing on 505 of 505 rows; `total_amount` is
never recomputed anywhere; there is no join fan-out (the fact table alone, and joined to all four
dimensions, both return 474 rows and $158,044.29); and the pipeline is **byte-identical across runs**
by MD5, the only diffs being output paths and timestamps.

---

## Documentation integrity: `solution/scripts/check_readme_numbers.py`

An earlier revision of this README carried eight stale headline numbers — the top store, the top
customer, and three of the four figures in the revenue reconciliation it presented as its own
proof-of-work. None were arithmetic errors. They were correct output from an older pipeline, copied
into prose by hand, and never revisited when the code moved underneath them.

Hand-copied numbers rot silently, and a reviewer who spot-checks one figure and finds it wrong stops
trusting the ones that are right. The structural fix is not to be more careful; it is to make
documentation staleness a **build failure**.

Every checked figure above is wrapped in a pair of HTML comments, invisible in rendered Markdown:

```
... net revenue of <!-- fig:net_revenue -->$158,044.29<!-- /fig --> across ...
```

The script extracts the literal text a human reads, normalises it, and compares it against a value
resolved live from `analytics.json`, `audit_report.json`, the lineage CSV, the warehouse database,
`solution/src/config.py`, or a live `pytest` collection. The mapping from marker id to source of truth lives
in the script and **never in the README**, so a number cannot be verified against itself. It also
fails if a registered figure stops being cited, because a marker broken during an edit is exactly how
a figure quietly stops being checked.

```bash
cd solution
python scripts/check_readme_numbers.py --readme ../README.md   # this document
python scripts/check_readme_numbers.py                         # solution/README.md
python scripts/check_readme_numbers.py --output-dir /tmp/run --readme ../README.md
python scripts/check_readme_numbers.py --list                  # show the figure registry
python scripts/check_readme_numbers.py -v                      # print every figure, not just failures
```

Exit codes: `0` every figure current · `1` at least one stale, unknown or uncited · `2` artifacts
missing (run the pipeline first).

**Both** documents are gated. `scripts/verify_submission.py` runs the check against this README and
against `solution/README.md`, and then asserts that the two cite the *same set* of figure ids — so
one cannot be updated while the other silently keeps an old number, and a figure cannot be dropped
from one document to make a failure go away. Wire the same two invocations into CI directly after
`pytest`.

---

## Trade-offs and what would change in production

**Accepted here, deliberately:**

- **SQLite, not a warehouse.** Correct for a single-file, reproducible deliverable a reviewer can
  open. The SQL is standard enough to port; `dim_date` generation and the `strftime` CHECKs are the
  only dialect-specific parts.
- **Money as `REAL`.** Integer cents would be strictly more correct. The source is 2-decimal text and
  every comparison is tolerance-based to the cent, so the exposure is bounded and stated.
- **Full reload, not incremental.** At 505 rows a merge strategy would be complexity with no payoff.
  The surrogate keys are what make an incremental load an additive change later.
- **`dim_product` is Type 1.** PR-02 proves a Type 2 dimension is warranted, but the source carries
  no effective date on the price, so validity ranges would have to be invented. The conflict is
  reported instead of guessed at.
- **Quarantine is a directory of CSVs.** Fine for a reviewer with a shell; in production these become
  a table with a review workflow, because a quarantine nobody triages is just deletion with extra
  steps.

**What would change on a real deployment:**

1. **Orphan handling becomes a late-arriving-dimension pattern.** TX-04/TX-05 quarantining is right
   for a one-shot batch, but four sequential unknown store IDs immediately after the last real store
   means the store master is stale, not that the transactions are bad. In production those rows park
   in a pending table and replay automatically when the dimension refreshes.
2. **Thresholds move out of code.** `RETURN_RATE_ALERT_THRESHOLD`, `PRICE_TOLERANCE` and
   `RECENT_WINDOW_DAYS` are already centralised in `solution/src/config.py`; the next step is a config table
   the business owns, so changing the alert level is not a deployment.
3. **The defect catalog becomes a monitored SLA.** `assert_all_expected_defects_found()` compares
   against counts known from the seed. Against live data those counts are unknown, so the same
   machinery becomes anomaly detection: alert when a defect class's rate moves outside its trailing
   band. That catches the *new* defect rather than the known one.
4. **Type-2 history on `dim_product` and `dim_store`**, with `valid_from` / `valid_to` and a current
   flag, once the source can supply effective dates.
5. **Orchestration and idempotency.** Airflow or similar, with the run keyed on `AS_OF_DATE` so
   re-running a day is safe. The pipeline is already deterministic, which is the hard prerequisite.
6. **A discount dimension.** TX-03 exists because promotions flow through a schema with nowhere to
   record them. The real fix is upstream: capture the promotion id, so `discount_amount` becomes an
   explained number rather than an inferred one.

---

## Repository layout

```
README.md                      <- you are here: the canonical reviewer guide
requirements.txt               pandas, numpy, pytest
scripts/
  verify_submission.py         the one command that proves this submission

solution/                      ===== THE SUBMISSION =====
  data/raw/                    the three supplied CSVs (unchanged)
  scripts/
    seed_data.py               the generator that produced data/raw (unchanged)
    check_readme_numbers.py    documentation-integrity gate
  src/
    config.py                  paths, AS_OF_DATE, thresholds, shared vocabulary
    defects.py                 the 17 specs: detection, decision, rationale, expected count
    audit.py                   AuditLog, DefectRecord, the coverage proof
    io_utils.py                dtype=str CSV reads, atomic JSON writes
    profiling/                 generic profiler + column checks (runs BEFORE cleaning)
    cleaning/
      rules.py                 shared date + currency parsers (TX-01, TX-02)
      stores.py                ST-01, ST-02, ST-03
      products.py              PR-01, PR-02, PR-03, PR-04
      transactions.py          TX-03 .. TX-10, plus the 505-row lineage file
    warehouse/
      schema.sql               DDL, with the grain and every CHECK explained inline
      loader.py                dims -> fact, one transaction, FK + tie-out verification
    analytics/
      queries.py               six named SQL constants + the metric registry
      runner.py                executes and serialises to analytics.json
    pipeline.py                six-stage orchestration + the coverage gate
  tests/                       87 tests, including a golden end-to-end run
  output/                      generated artifacts
  README.md                    the same document as this one, path-relative to solution/

dashboard/                     Next.js evidence dashboard over solution/output/dashboard_bundle.json

data/                          SUPERSEDED copy of solution/data/raw (byte-identical; unused)
output/                        SUPERSEDED artifacts from the first attempt -- see solution/output/
src/                           SUPERSEDED first attempt -- every module raises on import
tests/                         SUPERSEDED first attempt -- collects nothing
```

Everything the pipeline reads and writes lives under `solution/`. The four superseded entries above
are the first attempt's tree, kept for the reason given below. Read `solution/output/analytics.json`,
not `output/analytics.json`: the root copy predates the current metric registry and still uses the
old metric ids (`mom_revenue_by_category`, `avg_txn_value_by_region`) instead of the contracted
`mom_growth_by_category` and `aov_by_region`.

Every line that handles a defect carries a `# DEFECT: <CODE>` tag, which is what the dashboard's
code-links feature greps for.

### Why `src/` and `tests/` are still here at the root

They are the first attempt at this challenge. They are kept rather than deleted because most of what
this submission argues for is only persuasive next to the version that got it wrong, and because a
repository that quietly erases its own wrong turn is harder to trust than one that labels it. The
five named bugs are listed in `src/__init__.py`, and each one is now defended by the maintained
suite. Two of them — the unconditional `zfill` and the PR-02 price election — were re-applied
verbatim as mutations M12 and M2 while this document was written, and both now fail the build (see
*Mutation testing* above):

| Root `src/` (superseded) | What went wrong | Fixed in |
|---|---|---|
| `cleaner.py` | Recomputed `total_amount = unit_price × quantity`, destroying TX-03 and overstating revenue by <!-- fig:discount_total -->$961.48<!-- /fig --> | `solution/src/cleaning/transactions.py` |
| `cleaner.py` | One `pd.to_datetime(errors="coerce")` call dropped the 20 TX-01 rows, which were then misreported as future dates | `solution/src/cleaning/rules.py` |
| `cleaner.py` | Hardcoded `NY -> "East"` where the column's vocabulary says `Northeast`, inventing a fifth region | `solution/src/cleaning/stores.py` |
| `cleaner.py` | `drop_duplicates(subset=["product_id"])` swallowed PR-02, so the P005 price change was never reported | `solution/src/cleaning/products.py` |
| `cleaner.py` | `zfill(5)` applied unconditionally to every ZIP | `solution/src/cleaning/stores.py` |
| `analytics.py`, `loader.py`, `profiler.py` | Superseded wholesale | `solution/src/analytics/`, `warehouse/`, `profiling/` |

Neutralisation is deliberate rather than cosmetic. Importing anything under the root `src/` raises
immediately with the path of the maintained module; the root `tests/` package collects nothing and
says why in the pytest session header. Nothing re-exports the new code from the old location, because
a transparent forward would make the wrong import path work and hide the duplication instead of
surfacing it. `scripts/verify_submission.py` asserts both behaviours, so the shims cannot quietly rot
back into something importable.

One consequence worth stating: `solution/tests/` and the root `tests/` are both importable packages
literally named `tests`, so pytest derives the same module name for both conftest files. The root
`tests/conftest.py` releases that name once it has loaded, which is why `pytest` typed at the
repository root still runs the 87 submitted tests instead of aborting on an import collision. The
mechanism and the reasoning are commented in that file.
