# Interview prep — ten hard questions and the answers

Every figure below is from the verified pipeline run. Ranked 1 (strongest) to 10.

---

## 1. If I recomputed `total_amount` as quantity × unit_price, the data would be internally consistent. Why is that wrong, and what would it cost?

Because the inconsistency isn't an error — it's a discount. Twenty transactions report a total 5–20% below list, and that reported total is what the customer was actually charged. It is the authoritative revenue figure; `quantity × unit_price` is merely what the line *would* have cost at list price.

Recomputing raises net revenue from **$158,044.29 to $159,005.77** — $961.48 of revenue that no customer ever paid. That's a 0.61% overstatement: too small to trip anyone's smell test, large enough to misstate the number. Every downstream metric inherits it.

The pipeline instead keeps `total_amount` verbatim, adds `extended_amount` as the list value, exposes `discount_amount = extended_amount − total_amount`, and sets `has_discount`. Nothing is overwritten and no row is dropped. The `revenue_reconciliation` metric then ties gross list value → discounts → returns → net, so the $961.48 is visible as a finding rather than absorbed silently.

**Follow-up you'll get:** *"How do you know it's a discount and not a data error?"* — I don't, with certainty. What I know is that one interpretation preserves the source and flags the anomaly, and the other destroys evidence to make a number look tidy. The reversible choice wins, and the flag lets a business owner adjudicate.

---

## 2. P005 appears twice. Why isn't that a duplicate, and why does `dim_product` carry a price no transaction ever rang at?

Because the two rows differ. P012's duplicate is byte-identical — a bad extract, safe to drop. P005's two rows differ only in `unit_price`: $141.61 and $150.11. That's not a duplicate, it's an **undocumented price change**, and `drop_duplicates(subset=['product_id'])` would silently pick one based on row order. The CSV is shuffled, so it happens to put $150.11 first — meaning the naive path elects a price by accident.

The resolution is a general `resolve_duplicate_keys` routine that partitions colliding keys into *identical payload* (true duplicate, drop) and *conflicting payload* (a data event, resolve and flag). `dim_product` takes the higher price deterministically by MAX, never by file order, and carries `price_conflict = 1`.

As for why the dimension holds a price nothing sold at: **all 19 P005 fact rows ring at $141.61, and none at $150.11.** That's the evidence the increase post-dates the transaction window. The dimension holds the current list price; the fact holds the transacted price. If the fact read its price from the dimension, historical revenue would silently reprice itself every time a product's list price changed — which is the entire argument for storing price on the fact.

---

## 3. You report two return-rate denominators. Which stores breach 10% under each, and does the choice change who gets flagged?

Three stores breach, and **it's the same three either way**:

| Store | Unit-based | Transaction-based |
|---|---|---|
| S006 Lakeside Shopping Ctr | 13.73% | 12.50% |
| S015 Alderwood Mall | 13.51% | 15.38% |
| S008 Galleria at Crystal Run | 10.48% | 11.63% |

Note S015 and S006 swap rank between the two — the ordering is not stable even though membership is.

The challenge's wording ("return transactions ÷ total transactions") is ambiguous about units versus orders, so I report both rather than picking one in a `WHERE` clause and hoping nobody asks. The flag is applied to the unit-based rate; a 10% threshold is more conventional on units.

One deliberate deviation, documented in the metric's definition note: I use `returned / (sold + returned)` rather than `returned / sold`. The first is a proportion bounded at 100%; the second is an unbounded ratio that exceeds 100% whenever a return lands in a later period than its sale. Under the strict reading S006 would be 15.91% rather than 13.73% — flag membership is identical, so the choice is defensible either way, but it is stated rather than assumed.

---

## 4. June 2026 shows a 98% revenue collapse. What happened to the business?

Nothing. **June has one day of data** — 2026-06-01 — against March's 27. The comparison is a one-day month against a full one, so the percentage is an artifact of where the extract was cut, not an operational signal.

That's why the metric emits `days_with_data` on every row and states the caveat in its definition note. The same effect produces the +403.23% figure for Food & Beverage in April.

I rejected two alternatives: suppressing the boundary months hides real revenue and makes this metric's totals disagree with every other metric's; normalising to revenue-per-day makes the months comparable but quietly changes what the metric *is*. The challenge asked for month-over-month growth, so the honest fix is disclosure, not redefinition.

---

## 5. Account for all 505 transaction rows. Where did the 31 that aren't in `fact_sales` go?

| Disposition | Reason | Rows |
|---|---|---|
| Kept | — | 474 |
| Quarantined | TX-04 orphan `store_id` | 5 |
| Quarantined | TX-05 orphan `product_id` | 3 |
| Quarantined | TX-07 zero quantity | 5 |
| Quarantined | TX-08 future date | 3 |
| Dropped | TX-09 exact duplicate | 15 |
| **Total** | | **505** |

The five reasons are mutually disjoint — verified pairwise, no double-counting. Every source row is written to `output/quarantine/transactions__lineage.csv` with its `source_row` index (0–504, no gaps, no repeats) and its disposition, so the budget is reproducible rather than asserted.

Quarantine means the rows survive as files you can inspect — nothing is deleted. Orphans are excluded rather than loaded against an "Unknown" dimension member, because a fake member would let broken referential integrity masquerade as valid data in every aggregate.

---

## 6. Show me the line that decides a null `customer_id` is a guest rather than an error — and why keep those rows?

`src/cleaning/transactions.py`, tagged `# DEFECT: TX-06`. Forty rows arrive with a null `customer_id`; they get `customer_id = "GUEST"` and `is_guest = True`.

The reasoning is that a null customer is not a defect — it's a **guest checkout**, a real transaction by a real person who didn't have an account. Dropping those 40 rows deletes revenue that genuinely occurred. Recognising that a null can be semantically meaningful, rather than reflexively treating it as missing data, is the actual judgment here.

The cost is stated too: `dim_customer` gets one GUEST member, not 40 anonymous ones, because we can't know how many distinct people those rows represent. That's why `top_customers_lifetime` excludes GUEST — including it would create an artificial whale that dominates the leaderboard by construction. The exclusion is in the definition note, not hidden in a `WHERE` clause.

---

## 7. Two stores had a null region. What did you impute, and why not "East"?

S013 (Cascade Station) and S014 (Lloyd Center), both Oregon, both imputed to **West**.

"East" is the interesting part of the question, because it's the mistake an earlier version made. The column's own vocabulary is `Northeast`, `Midwest`, `South`, `West`. Mapping NY to "East" invents a fifth value that doesn't exist in the data — and because average-order-value is grouped by region, it silently splits the Northeast into two buckets. Every regional figure would be wrong, and nothing would error.

So the state→region map is **built at runtime from the values the column already contains**, not from a hardcoded dictionary. `dim_store` carries `region_is_imputed = 1` on those two rows, and AOV returns exactly four regions. Any imputed value must come from the observed vocabulary, or you're not filling a gap — you're inventing a category.

---

## 8. S003's zip is `0938`. You padded it to `00938`, but that isn't a real New York zip. Why present a wrong value?

Because padding restores the likely original encoding without claiming the result is correct. A four-character zip is the classic signature of a spreadsheet eating a leading zero, so `00938` is the most probable pre-corruption string — but Greece, NY is really 14626, so `00938` is almost certainly still wrong.

The pipeline therefore does two things: pads to five characters, and sets `zip_is_suspect = 1` in `dim_store` so a data steward can verify the location against the source system. It restores structural validity and flags semantic uncertainty. What it doesn't do is present a repaired-looking value as if it were confirmed.

The padding is also guarded: only values matching a digits-only pattern are padded. An unconditional `zfill(5)` would turn `"N/A"` into `"00N/A"` — inert on this dataset, since every other zip is already five characters, but a latent bug the moment the data changes.

---

## 9. Twenty dates were in three formats. How do you know they parsed correctly rather than silently parsing wrong?

This is the right question, because **row counts cannot detect a misparse.** A wrongly parsed date is still a date; nothing is dropped, no count changes, and the error surfaces months later as a transaction in the wrong month.

Ten rows are `MM/DD/YYYY`, ten are `DD-MM-YYYY`, the rest ISO. A single `pd.to_datetime(errors="coerce")` either NaTs them or guesses. The pipeline parses per-format against an explicit ordered ladder from `config.DATE_FORMATS`, records which format each row matched, and asserts that zero rows are left unparsed.

The disambiguation is deterministic rather than lucky: the two ambiguous formats use **different separators** — slash for US, hyphen for EU — so the ladder can never confuse them. The genuinely ambiguous pair proves it: `01-04-2026` and `04/01/2026` both resolve to 2026-04-01, correctly, by different routes.

For what it's worth, this was verified independently by replaying the generator with provenance instrumentation and comparing all 20 parsed dates against their pre-corruption values. 20 of 20 correct, none dropped.

---

## 10. What would make `line_level_delta` non-zero, and what can it not detect?

`line_level_delta` is `SUM(extended_amount − discount_amount − net_amount)` over non-returns; `aggregate_delta` is `(gross_list_value − discount_total) + returns_value − net_revenue`. Both are $0.00 today. They go non-zero if any row's three money columns stop agreeing — a mis-applied discount, a rounding drift, a bad load. A one-cent error on a single row surfaces as `-0.01`.

What they cannot detect is a **uniform rescaling** — multiply every money column by the same factor and the internal relationships still hold. That case is covered elsewhere: `loader.verify_warehouse()` compares the warehouse's revenue sum against the cleaned source total and aborts the load on any disagreement beyond half a cent.

This distinction is worth stating because the metric's earlier version was worse than useless. It computed `SUM(net WHERE is_return=0) + SUM(net WHERE is_return=1) − SUM(net)`, which is identically zero for any data — `is_return` partitions the rows, so the expression is an algebraic tautology. It reported $0.00 after $79,000 of revenue was deliberately injected into a copy of the warehouse. A check that cannot fail isn't a check; it's decoration that buys false confidence.

---

## Before you demo this

The assistant is grounded on the pipeline bundle — the defect catalog, audit ledger, metrics, and annotated source. It does **not** have test results. Ask "how many tests pass?" or "tell me about the mutation testing" and it will correctly decline, because those figures aren't in its context.

That's the guardrail working as designed, and it's worth saying so out loud if it happens. But don't open with one. Test-suite questions belong to the README and `python scripts/verify_submission.py`.
