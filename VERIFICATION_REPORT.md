# ADVERSARIAL VERIFICATION REPORT

**Auditor:** Agent 10 (independent verification)
**Date:** 2026-08-04
**Scope:** `solution/` pipeline, warehouse, analytics, tests, docs, and `dashboard/` bundle
**Method:** independent recomputation from the raw CSVs in pandas, regeneration of the seed with
row-level provenance, direct SQLite interrogation, double-run determinism diff, and 18 mutation
experiments against scratch copies under `/tmp`. No project file was modified.

---

## VERDICT

**The engine is sound. The packaging is not.**

The computational core of this submission is genuinely good, and I tried hard to break it. All six
metrics reproduce my independent pandas recomputation to the cent. All 17 defect counts reconcile
against the seed. The 505-row budget conserves exactly. `total_amount` is never recomputed anywhere.
All 20 TX-01 dates parse to the *correct* calendar date, including both genuinely ambiguous strings.
The star schema carries real, load-bearing CHECK constraints that rejected three of my sabotage
attempts outright. The pipeline is byte-for-byte deterministic across runs.

What is broken is everything wrapped around that core:

1. **The README — the first thing a reviewer reads — contains eight wrong headline numbers**,
   including the wrong top store, the wrong top customer, and three of the four figures in the
   revenue reconciliation it presents as its proof-of-work. These are stale numbers from an earlier
   run. This is precisely the failure class the build contract flagged as credibility-destroying
   (§1, "Known bugs in the PREVIOUS solution", item 2) and it has been repeated.
2. **The live dashboard's flagship feature is broken.** `dashboard/public/data/bundle.json` ships a
   17-entry `code_index` but no `source_files`, so every defect's code viewer renders the fallback
   error string that the component itself defines for exactly this case.
3. **`pytest` destroys a pipeline artifact in the repo**, and `--output-dir` is not honoured for
   that artifact. A reviewer following the README's own Quickstart (run pipeline, then run tests)
   ends up with a 2-row fixture file where the 505-row lineage proof should be.

**Ambiguous / defensible-but-undocumented:** the unit-based return-rate denominator differs from the
contract's literal wording (documented, and it changes no flag); the month-over-month percentages
compare a 1-day month against a 31-day month with no caveat; `reconciliation_delta` is an algebraic
tautology that cannot detect the bug its own comment claims it detects.

**Findings: 3 blocker, 5 major, 6 minor, 4 nit.**

---

## FINDINGS TABLE

| # | Sev | Area | What's wrong | Evidence | Suggested fix |
|---|-----|------|--------------|----------|---------------|
| F1 | **blocker** | Docs | `README.md` states 8 wrong headline numbers: top store, top customer (3 fields), Northeast AOV, and 3 of 4 reconciliation figures. All are stale values from an earlier pipeline version. | Full side-by-side below. e.g. README "The Domain (South) $2,072.94"; actual **Galleria at Crystal Run (Northeast) $6,770.08**. README `gross_list_value $170,816.34`; actual **$168,957.80**. | Regenerate every figure from the current `output/analytics.json`. Better: template them, or add a doc-check test that asserts README numbers against the JSON. |
| F2 | **blocker** | Dashboard | `dashboard/public/data/bundle.json` has `code_index` (17 entries) but **no `source_files` key**. `CodeViewer.tsx:149-150` renders "`code_index` references it but `source_files` does not carry it" for every defect. The code-links feature — the dashboard's headline differentiator — is dead on the deployed site. | `python -c "json.load(...)"` → top keys lack `source_files`. `DefectExplorer.tsx:307` passes `bundle.source_files ?? {}`. Ironically `bundle.mock.json` **does** carry 4 source files, so local mock mode works and production does not. | `solution/src/pipeline.py` is missing the `_build_source_files()` helper that the *root* `src/pipeline.py` has (see F4). Port it back and regenerate the bundle. |
| F3 | **blocker** | Pipeline / tests | `clean_transactions(..., lineage_dir=QUARANTINE_DIR)` defaults to the **import-time hardcoded project path**. `pipeline.py:327` does not pass `lineage_dir`, so `--output-dir` is ignored for `transactions__lineage.csv`; and the test suite (8 call sites, none passing `lineage_dir`) **overwrites the real 505-row lineage file with 2 rows of fixture data** (`TXN001`, `TXN002`). | Proven: ran `python -m src.pipeline --output-dir /tmp/verify_out3`; the project file's mtime changed and grew to 506 lines, while `/tmp/verify_out3/quarantine/` contains no lineage file. Before my run the committed file contained only the pytest fixture rows. | `pipeline.py` must pass `cfg.quarantine_dir`. Tests must pass `lineage_dir=tmp_path` (or `None`). Consider making the parameter required. |
| F4 | **major** | Repo hygiene | Two divergent copies of the codebase. `solution/src/` and root `src/` differ in 3 files, **each having features the other lacks**: `solution/` has `column_units` + correct `sql_ref` casing; root has `_build_source_files`. Root `src/` additionally carries the previous attempt's dead modules (`cleaner.py`, `analytics.py`, `loader.py`, `profiler.py`). | `diff -rq src solution/src`. Root `src/analytics/queries.py` 15,387 B (Aug 3 20:42) vs `solution/` 22,080 B (Aug 4 13:23). | Pick one tree. Delete or clearly quarantine the other. A reviewer who opens the wrong `src/` reads the wrong code. |
| F5 | **major** | Artifacts | `solution/output/` is **stale**. `analytics.json` and `dashboard_bundle.json` there were generated by an older runner and carry only `definition_note`/`row_count`/`rows` — **no `title`, `description`, `sql`, `sql_ref`, or `column_units`**, violating contract §6 and §7b. A fresh run produces all of them. | `solution/output/analytics.json` `generated_at: 2026-08-03T17:06`; metric keys = 3. Fresh run: 8 keys. Row *values* are identical, so only the shape is stale. | Re-run the pipeline in place and commit the refreshed `output/`. |
| F6 | **major** | Docs / entry point | `solution/README.md` is a 3-line stub redirecting to `../README.md` — which per the build contract is the **previous attempt's** documentation, and whose Quickstart (`python -m src.pipeline` from repo root) runs the *other* code tree. The contract (§3) assigns a real README to `solution/`. | `solution/README.md` is 162 bytes, 3 lines. | Move the (corrected) documentation into `solution/README.md`. |
| F7 | **major** | Analytics | `reconciliation_delta` is an **algebraic tautology**. `SUM(net WHERE is_return=0) + SUM(net WHERE is_return=1) − SUM(net)` is identically zero for any data, because `is_return ∈ {0,1}` partitions the rows. The SQL comment claims "If it does not, the pipeline has an arithmetic bug somewhere between cleaning and loading" — **that statement is false**. | Proven: inflated every money column by 50% in a copy of the DB (both fact CHECK constraints still satisfied); delta stayed `0.00` while $79k of revenue was invented. Also survives as a mutation when replaced with the literal `0.0` — no test and no pipeline gate notices. | Compute the delta that is *not* free: `SUM(extended_amount − discount_amount − net_amount)` for non-returns, plus `gross_sales_net_of_discount + returns_value − net_revenue`. Both currently evaluate to 0.00 correctly. |
| F8 | **major** | Tests | Three defect codes have **no test at all**: TX-05, TX-09 (zero mentions anywhere in `tests/`), and PR-04 (mentioned only in a docstring). `tests/test_cleaning.py:19` claims "PR-04 Zero price — flagged" but no assertion exists. README:168 claims tests cover "zero prices (PR-04)" and "orphan exclusions (TX-04/**05**)" — both false. | `grep -c` per code across `tests/`: TX-05 = 0, TX-09 = 0, PR-04 = 2 (both docstrings). | Add the three tests, or correct the docstring and README claims. |
| F9 | minor | Analytics | MoM percentages compare **partial months** with no caveat. 2026-03 has 27 days, 2026-06 has **1 day** (a single date, 2026-06-01). Hence "−95.92%", "−98.73%", "−99.26%" for June and "+403.23%" for Food & Beverage in April. None of this is a business signal and the `definition_note` does not say so. | `dim_date`/fact join: 2026-03 = 27 days / 125 txns; 2026-06 = 1 day / 4 txns. | Add a `days_in_month_with_data` column, or state the partial-boundary caveat in `definition_note`. |
| F10 | minor | Analytics | Unit-based return rate uses `returned / (sold + returned)`, but contract §6 specifies `SUM(returned units) / SUM(sold units)`. The `definition_note` says "returned units / total units", so it is *self*-consistent and honestly documented — but it is not the contracted formula. | S006: 14/(88+14) = 13.73% as reported; contract formula gives 14/88 = 15.91%. **No flag membership changes** under either denominator. | Either follow the contract or add one sentence noting the deliberate deviation and why. |
| F11 | minor | Contract | Two metric IDs do not match the contract's required names: `mom_growth_by_category` → shipped as `mom_revenue_by_category`; `aov_by_region` → shipped as `avg_txn_value_by_region`. | Contract §6 "Required metric ids". | Rename, or note the deviation. Low risk but it is a stated binding interface. |
| F12 | minor | Tests | Tests are **100% synthetic fixtures**. Nothing asserts against `data/raw/` or the real end-to-end outputs. There is no golden-file test, so no test would notice if kept rows went from 474 to 400 or revenue moved by $10k. | `grep -rn "data/raw\|RAW_DIR\|read_csv" tests/` → no hits. | Add one end-to-end test pinning 474 rows / $158,044.29 / 17-of-17 coverage. This single test would kill 3 of the 5 surviving mutations. |
| F13 | minor | Artifacts | `solution/output/.warehouse.db.l6tw7vnp.building` — a zero-byte orphan temp file from a failed run, committed into the deliverable. | `ls -la solution/output/`. | Remove; add the pattern to `.gitignore`. |
| F14 | minor | Robustness | ST-01's digit-guard on `zfill` is correct but **untested**. Replacing the guard with unconditional `zfill(5)` — the previous solution's named bug #5 — passes all 27 tests *and* the full pipeline. It is behaviourally inert only because every other ZIP in this dataset is already 5 characters. | Mutation S5. | Add a fixture with a non-paddable ZIP (`"N/A"`) and assert it is left alone. |
| F15 | nit | Analytics | `reconciliation_delta` serialises as `-0.0` in the fresh run (`0.0` in the stale artifact). Renders as "-0" in a table. | `/tmp/verify_out1/analytics.json`. | `+ 0.0` or `abs()` before rounding. |
| F16 | nit | Audit | The `quarantine/` directory conflates *dropped rows* with *evidence snapshots*. Products: 4 quarantined rows but only 2 actual drops (P005 and P027 survive). A reader summing the CSVs gets 32 − 4 = 28 ≠ 30. | File row counts vs `products_clean.csv`. | Add a `disposition` column to quarantine CSVs, as the transactions lineage file already does well. |
| F17 | nit | Docs | README:50 describes the ST-02 survivorship rule as a "lexicographical tie-breaker". The implemented (and correct) rule is fewest-nulls → earliest `opened_date` → lexicographic name. The outcome is right; the description understates a three-stage rule as one stage. | `src/cleaning/stores.py:355-362`. | One-line correction. |
| F18 | nit | Docs | README:135 says the top-store SQL filters `dim_date.full_date BETWEEN :window_start AND :as_of_date` — correct — but never states the window is **30 days inclusive** (`AS_OF − 29`), which is the one thing a reader would want pinned down. | `src/config.py:191`. | State the boundary convention. |

---

## INDEPENDENT METRIC RECOMPUTATION

I derived each metric from `data/raw/*.csv` in pandas from scratch, applying the contract's locked
decisions, **before** reading `queries.py`. Side by side:

| Metric | My independent value | Pipeline value | Verdict |
|---|---|---|---|
| **Kept transaction rows** | 474 | 474 | MATCH |
| **Net revenue** `SUM(total_amount)` | $158,044.29 | $158,044.29 | MATCH |
| **top_stores_recent_30d** #1 | Galleria at Crystal Run, $6,770.08, 17 txns, 0 returns | identical | MATCH |
| … #2–#5 | Southpark $6,555.48 / Eastview $5,865.13 / Lloyd $4,979.12 / Domain $4,938.18 | identical | MATCH |
| **Window anchoring** | `[2026-05-04, 2026-06-02]`, 30 days inclusive, 181 rows | same | MATCH — anchored on `AS_OF_DATE`, not wall clock. A `[AS_OF−30, AS_OF]` reading would reorder #1/#2, so the convention matters; it is implemented as documented in `config.py:191`. |
| **mom_revenue_by_category** | 22 rows, 6 categories × 4 months (2 legitimate absences) | 22 rows, identical to the cent | MATCH |
| **LAG boundary** | first month per category → `NULL` prev, `NULL` pct | same | MATCH — correct, no division by zero |
| **Vanishing categories** | Food & Beverage and Office Supplies absent from 2026-06 | same | **CORRECT** — verified from the fact table: both had genuinely **zero** June transactions. Nothing silently dropped. |
| **return_rate_by_store** | all 15 stores, txn + unit rates | identical to 0.01pp | MATCH |
| — unit denominator | `returned/(sold+returned)` | same | Internally consistent; see F10 re contract wording |
| — >10% flag | applied to **unit** rate: S006 13.73, S015 13.51, S008 10.48 | same 3 stores | MATCH — flag is on the rate the `definition_note` claims |
| **avg_txn_value_by_region** | NE 389.05 / South 384.49 / MW 375.82 / West 364.02 | identical | MATCH — 4 regions, not 5; no invented "East" splitting Northeast |
| **top_customers_lifetime** | CUST0213 $3,077.96 (4 txns) … CUST0118 $1,655.79 | identical | MATCH |
| — GUEST exclusion | GUEST absent; 40 guest rows retained in fact | `WHERE c.is_guest = 0`; GUEST absent | MATCH — no other sentinel leaks in; `dim_customer` = 228 real + 1 GUEST = 229 |
| **revenue_reconciliation** | gross $168,957.80 − disc $961.48 = $167,996.32; + returns −$9,952.03 = **$158,044.29** | identical | MATCH — the *columns* tie out to the cent by hand. Only the `reconciliation_delta` column itself is vacuous (F7). |

Only discrepancy across all six metrics: `CUST0287.avg_order_value` = 706.43 (pipeline) vs 706.42
(mine) — `2825.70 / 4 = 706.425`, a half-cent rounding-mode difference. Not a finding.

### Reconciliation delta — is it real?

No. `SUM(net WHERE is_return=0) + SUM(net WHERE is_return=1) − SUM(net)` ≡ 0 for all data.
Demonstrated on a copy of the warehouse: after scaling `unit_price`, `extended_amount`,
`discount_amount` and `net_amount` by 1.5 (both row-level CHECKs still satisfied), the metric
reported `gross 253,438.74 / net 237,068.36 / **delta 0.00**`.

**Mitigating:** the genuine tie-out does exist elsewhere and is load-bearing —
`loader.py:verify_warehouse()` check 4 compares `SUM(fact_sales.net_amount)` against
`SUM(cleaned.total_amount)` at half-a-cent tolerance and aborts the load on failure, and
`schema.sql` enforces `ABS(discount_amount − (extended_amount − net_amount)) ≤ 0.01` and
`ABS(extended_amount − quantity*unit_price) ≤ 0.01` per row. These are real. The problem is that
the *metric advertised to the reviewer as the proof* is the one part that proves nothing.

---

## ROW-BUDGET RECONCILIATION

Every source row is accounted for. Verified from `output/quarantine/transactions__lineage.csv`
(one row per source row) **and** independently reproduced by my own disposition logic.

### transactions — 505 rows
| Disposition | Reason | Rows |
|---|---|---|
| kept | — | **474** |
| quarantined | TX-04 orphan store | 5 |
| quarantined | TX-05 orphan product | 3 |
| quarantined | TX-07 zero quantity | 5 |
| quarantined | TX-08 future date | 3 |
| dropped | TX-09 exact duplicate | 15 |
| **Total** | | **505** ✓ |

- `source_row` values form exactly `0..504`, no gaps, no repeats.
- Kept-row transaction-ID set == `transactions_clean.csv` ID set == `fact_sales` (474 rows).
- The five drop reasons are **mutually disjoint** (verified pairwise overlap matrix: all zero), so
  505 − 31 = 474 with no double-counting.
- **My independently computed disposition agrees with the lineage file on all 505 of 505 rows.**

### products — 32 rows
32 raw → 30 in `dim_product`. Drops: 1 (PR-01 exact duplicate of P012) + 1 (the losing P005 row).
Note the quarantine CSVs hold 4 rows because PR-02 (2) and PR-04 (1) are *evidence snapshots*, not
drops — P005 and P027 both survive. See F16.

### stores — 16 rows
16 raw → 15 in `dim_store`. Drop: 1 (ST-02 losing S007 row). ST-01's quarantine row is evidence;
S003 survives with `zip_is_suspect = 1`. `dim_store` region distribution: Northeast 5, West 3+2
imputed, South 3, Midwest 2 = 15.

### Defect counts — all 17 verified independently
Every count in the pipeline's 17/17 table reproduces exactly from my own scan of the raw CSVs:
ST-01 1, ST-02 1, ST-03 2, PR-01 1, PR-02 1, PR-03 5, PR-04 1, TX-01 20, TX-02 25, TX-03 20,
TX-04 5, TX-05 3, TX-06 40, TX-07 5, TX-08 3, TX-09 15, TX-10 30. **No count matches the catalog
while failing the row budget.**

---

## MUTATION-TESTING RESULTS

18 mutations applied to scratch copies under `/tmp`. "Caught by tests" = `pytest` exits non-zero.
"Caught by runtime" = the pipeline aborts. **Survivors are those that pass both.**

| # | Mutation | Tests | Pipeline | Result |
|---|---|---|---|---|
| M1 | TX-03: recompute `total_amount = qty × unit_price` (the catastrophe) | **caught** (8 fail) | — | killed |
| M2 | PR-02: elect **MIN** price instead of MAX | pass | **passes 17/17** | **SURVIVED** |
| M3 | Return-rate threshold `>` → `<` | **caught** | — | killed |
| M4 | `top_customers`: stop excluding GUEST | **caught** | — | killed |
| M5 | `RECENT_WINDOW_DAYS` 30 → 60 | pass | **passes 17/17** | **SURVIVED** |
| M6 | `reconciliation_delta` replaced with literal `0.0` | pass | **passes 17/17** | **SURVIVED** |
| M7 | Unit-rate denominator → `units_sold` only | **caught** | — | killed |
| M8 | AOV stops excluding returns | **caught** | — | killed |
| M9 | TX-09 dedup disabled | pass | **caught** (UNIQUE violation) | killed by schema |
| M10 | TX-05 orphan-product check disabled | pass | **caught** (`UnresolvedKeyError`) | killed by loader |
| M11 | PR-04 zero-price imputation removed | **caught** (2 fail) | — | killed |
| M12 | ST-01 `zfill` applied unconditionally (prev. bug #5) | pass | **passes 17/17** | **SURVIVED** (inert on this data) |
| M13 | ST-03 imputes invented `"East"` (prev. bug #3) | **caught** | — | killed |
| M14 | `AS_OF_DATE` → `date.today()` | **caught** | — | killed |
| M15 | TX-06 GUEST sentinel renamed | **caught** | — | killed |
| M16 | TX-10 returns filtered out | **caught** (8 fail) | — | killed |
| M17 | `PRICE_TOLERANCE` 0.01 → 10000 (TX-03 invisible) | **caught** | — | killed |
| M18 | 30-day window loses its upper bound | pass | **passes 17/17** | **SURVIVED** |

**Kill rate: 13/18 by tests+runtime combined; 11/18 by the test suite alone.**

**The 5 survivors — the honest measure of the suite:**

1. **M2 — PR-02 MIN instead of MAX.** Verified effect: `dim_product.P005.list_unit_price` becomes
   141.61 instead of 150.11. This is the exact finding PR-02 exists to surface, silently reversed,
   with the coverage table still reporting "PR-02 · Expected 1 · Detected 1 · OK". The most
   dangerous survivor, because the completeness proof gives false assurance.
2. **M6 — reconciliation delta hardcoded.** Confirms F7: the metric is untested and unfalsifiable.
3. **M5 / M18 — window boundary changes.** Both silently change the published top-5 store ranking.
   No test binds the window; the analytics tests use a synthetic 5-row fixture warehouse.
4. **M12 — unconditional ZIP `zfill`.** Behaviourally inert on this dataset (all other ZIPs are
   already 5 chars), so a latent robustness gap rather than a live bug — but it is verbatim the
   previous solution's named bug #5, and nothing would stop it coming back.

Positive note: M9 and M10 pass the tests but are stopped cold by the **warehouse** — the UNIQUE
constraint and `_resolve_key`'s refusal to load against an "Unknown" member. That is exactly what a
well-designed schema is for, and it is doing real work here.

---

## WHAT I VERIFIED AS CORRECT

So the reviewer knows this audit's coverage. Each of these was actively attacked, not assumed.

- **Raw data authenticity.** Regenerated `transactions.csv` from `seed_data.py` with the same seeds;
  the 505 rows are byte-identical to `data/raw/`. My ground truth is the real ground truth.
- **TX-03 — `total_amount` is never recomputed.** Grepped every `.py` and `.sql` in `src/`; the only
  matches are docstrings explaining why not. Verified in the DB: the 20 discount rows carry
  `net_amount` = reported (e.g. TXN10101: ext 430.24, disc 75.72, net 354.52), discount total
  $961.48, and revenue ties to the source sum at 0 cents.
- **TX-01 — all 20 mixed-format dates parse to the *correct* calendar date.** Compared each parsed
  date against the pre-mangling ISO value captured from a provenance-instrumented seed replay: 20/20
  correct, 0 dropped. Includes both genuinely ambiguous strings — `01-04-2026` → 2026-04-01 and
  `04/01/2026` → 2026-04-01 — resolved correctly. The format ladder is separator-disjoint, so the
  disambiguation is deterministic rather than lucky. **All 474 kept rows** carry the correct date.
- **ST-03 — region imputation uses only observed vocabulary.** `dim_store` contains exactly
  {Northeast, Midwest, South, West}; no "East". AOV-by-region returns 4 rows, not 5. The map is
  built from the column's own values at runtime (`build_state_region_map(out)`), not a hardcoded
  dict — which is why mutation M13 was caught.
- **PR-02 — dimension vs fact price separation.** Verified from the DB, not comments:
  `dim_product.P005.list_unit_price = 150.11`, while all 19 kept P005 fact rows ring at **141.61**.
  Raw data confirms all 20 source P005 transactions were at 141.61. Same pattern for PR-04:
  `dim_product.P027 = 126.96` (category median, `price_is_imputed = 1`) while 17 fact rows carry the
  transacted 195.34. No fact data laundered into master data.
- **ST-01 / ST-02.** S003 → `00938` with `zip_is_suspect = 1`. S007 → "Downtown Rochester" via an
  explicit three-stage ranked rule, not `keep="first"`.
- **No join fan-out.** `fact_sales` alone: 474 rows / $158,044.29. Joined to all four dimensions
  simultaneously: 474 rows / $158,044.29. Identical. `PRAGMA foreign_key_check` clean; zero
  unresolved keys.
- **Schema integrity is real, not decorative.** `PRAGMA foreign_keys` enforcement is probed at load
  time. Three separate sabotage attempts were rejected by CHECK constraints:
  `ABS(discount_amount − (extended_amount − net_amount)) ≤ 0.01`,
  `ABS(extended_amount − quantity × unit_price) ≤ 0.01`, and
  `(is_return=1 AND quantity<0) OR (is_return=0 AND quantity>0)`.
- **No integer division.** Every ratio in `queries.py` is float-forced (`CAST(... AS REAL)` or
  `* 100.0`) and every denominator is `NULLIF`-guarded. The guards are reachable: S009 and S014 have
  zero returns and return 0.00 rather than erroring; first-month `LAG` returns `NULL` cleanly.
- **Determinism / idempotency.** Two runs into different directories: `analytics.json`,
  `defect_catalog.json`, `profile_report.json`, all cleaned CSVs and all quarantine CSVs are
  identical, and `warehouse.db` is **byte-identical by MD5** (`0866dd33…`). Surrogate key assignment
  is stable. The only diffs are the output path and timestamps.
- **No wall-clock leakage.** `datetime.now()` appears only in `generated_at`/`started_at` metadata.
  All time-relative logic uses `AS_OF_DATE = 2026-06-02`. Mutation M14 confirms this is enforced.
- **`column_units` are correct and complete.** Every column of every metric has a unit declaration;
  no metric has a missing or extra key. Specifically checked the trap: all three `percent` columns
  hold 0–100-scaled values (`mom_change_pct` up to 403.23, `unit_return_rate_pct` up to 13.73), not
  sub-1 ratios. No mis-scaling.
- **`code_index` line numbers are accurate.** All 18 refs resolve to a real file and the cited line
  literally contains the matching `# DEFECT: <CODE>` tag. All 17 codes are tagged. (The refs are
  valid — the bundle just doesn't ship the files they point into. See F2.)
- **Bundle ↔ analytics agreement.** All six metrics' `rows` in `dashboard/public/data/bundle.json`
  match a fresh pipeline run exactly.
- **Test counts.** README's 8 / 14 / 5 = 27 breakdown is accurate, and 27 pass.
- **Defect coverage gate works.** A TX-05 regression makes the pipeline exit 2 with a precise
  diagnostic rather than producing partial outputs.

---

## THREE THINGS TO FIX, IN ORDER

1. **F1 — rewrite the README's numbers.** Eight wrong figures on the summary page. A reviewer who
   spot-checks one number against `analytics.json` finds a mismatch in seconds, and from that point
   distrusts everything else — including the parts that are genuinely excellent.
2. **F3 + F2 — stop `pytest` clobbering `output/quarantine/transactions__lineage.csv`, and ship
   `source_files` in the dashboard bundle.** One is a data-integrity bug in the deliverable's own
   artifacts; the other kills the dashboard's headline feature on the live URL.
3. **F5 + F4 — regenerate `solution/output/` and collapse the two divergent `src/` trees.** The
   committed analytics artifact is contract-non-compliant in shape while the code that would produce
   a compliant one sits right next to it.
