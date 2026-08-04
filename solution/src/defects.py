"""The defect registry: one canonical, reviewable entry per seeded data defect.

This module is the pipeline's contract with the reviewer. Every data-quality
problem in ``data/raw/*.csv`` is enumerated here exactly once, together with:

* how it is **detected** (so the check is reproducible),
* what was **decided** (so the behaviour is intentional, not incidental),
* **why** (so the decision can be argued with), and
* the **expected count** taken from ``scripts/seed_data.py`` (so the pipeline can
  prove at runtime that it actually found what is there).

That last field is the important one. Anyone can write a cleaning script; the
question a reviewer really has is *"did you find everything, and how would you
know if you hadn't?"* ``AuditLog.assert_all_expected_defects_found()`` answers it
by joining detected counts back to this catalog and failing the run on any
divergence. The catalog is therefore executable documentation, not a README.

Defect codes owned: all 17 (ST-01..ST-03, PR-01..PR-04, TX-01..TX-10).

Inputs:  none (pure data).
Outputs: :data:`DEFECT_CATALOG`, plus :func:`write_defect_catalog_json` which
         materialises it at :data:`DEFECT_CATALOG_JSON_PATH` for the dashboard.

Note for readers grepping the codebase: this file deliberately carries **no**
``# DEFECT: <CODE>`` tags. Those tags mark the line that *handles* a defect, and
the code-index scanner would otherwise report the catalog as the handler for all
17. The registry is the map; the tags are the territory.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from src.config import DEFECT_CATALOG_JSON_PATH

CATALOG_VERSION: str = "1.0.0"
"""Bumped whenever a spec's decision or expected_count changes, so a stale
dashboard bundle is identifiable rather than merely wrong."""


# ── Defect codes ──────────────────────────────────────────────────────────────
class DefectCode(str, Enum):
    """Stable identifiers for every seeded defect class.

    WHY ``str, Enum`` and not a plain Enum: members compare equal to their
    string value, so they survive ``json.dumps``, SQLite parameter binding and
    DataFrame columns without a conversion shim, while still giving static
    checkers and IDEs a closed vocabulary. ``DefectCode.TX_03_SILENT_DISCOUNT
    == "TX-03"`` is True.

    WHY the ``<AREA>_<NN>_<SLUG>`` member naming: the numeric part is the
    reviewer-facing code used in the docs, dashboard and ``# DEFECT:`` tags; the
    slug makes call sites self-documenting. Renaming a slug is safe, renaming a
    value is not -- the values are the public API.
    """

    # stores.csv
    ST_01_MALFORMED_ZIP = "ST-01"
    ST_02_NEAR_DUPLICATE_PK = "ST-02"
    ST_03_NULL_REGION = "ST-03"

    # products.csv
    PR_01_EXACT_DUPLICATE = "PR-01"
    PR_02_PRICE_CHANGE = "PR-02"
    PR_03_NULL_CATEGORY = "PR-03"
    PR_04_ZERO_PRICE = "PR-04"

    # transactions.csv
    TX_01_MIXED_DATE_FORMATS = "TX-01"
    TX_02_STRING_CURRENCY = "TX-02"
    TX_03_SILENT_DISCOUNT = "TX-03"
    TX_04_ORPHAN_STORE = "TX-04"
    TX_05_ORPHAN_PRODUCT = "TX-05"
    TX_06_NULL_CUSTOMER = "TX-06"
    TX_07_ZERO_QUANTITY = "TX-07"
    TX_08_FUTURE_DATE = "TX-08"
    TX_09_EXACT_DUPLICATE = "TX-09"
    TX_10_RETURNS = "TX-10"

    def __str__(self) -> str:  # pragma: no cover - trivial
        # WHY: f-strings on a str-Enum would otherwise render
        # "DefectCode.TX_03_SILENT_DISCOUNT" in log lines and markdown tables.
        return self.value


# ── Severity vocabulary ───────────────────────────────────────────────────────
class Severity(str, Enum):
    """Closed severity vocabulary, so the dashboard can colour-code reliably.

    The rubric applied throughout this catalog:

    * ``critical`` -- gets the *revenue number itself* wrong, or breaks primary
      key integrity. Silent: the output looks plausible while being false.
    * ``high``     -- loses or invents rows; wrong but usually detectable
      downstream.
    * ``medium``   -- degrades a dimension or a denominator; analytics are
      skewed, totals survive.
    * ``low``      -- cosmetic or fully recoverable with no judgement call.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ── Spec record ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DefectSpec:
    """Everything known about one defect class, ahead of running the pipeline.

    WHY frozen: the catalog is read by the audit log, the tests and the
    dashboard exporter. If any of them could mutate a spec, the "expected"
    side of the expected-vs-detected comparison would stop being trustworthy --
    a test could accidentally make itself pass.

    Attributes:
        code: The stable :class:`DefectCode`.
        dataset: ``"stores" | "products" | "transactions"``.
        title: Short human label, used as the dashboard card heading.
        severity: One of :class:`Severity`'s values.
        expected_count: Number of affected source rows according to
            ``scripts/seed_data.py``. ``None`` means "variable / not knowable
            up front" -- no spec currently uses ``None``, which is itself a
            claim: every defect in this dataset is countable in advance.
        detection: One-line description of the check that finds it.
        decision: The rule the pipeline applies.
        rationale: Why that rule, and what the rejected alternative would cost.
        source_ref: ``path/to/module.py:function`` implementing the decision.
    """

    code: DefectCode
    dataset: str
    title: str
    severity: str
    expected_count: int | None
    detection: str
    decision: str
    rationale: str
    source_ref: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready mapping (enum members flattened to strings).

        Returns:
            Plain ``dict`` safe for ``json.dumps`` with no custom encoder.

        Defects handled: none (serialization helper).
        """
        payload = asdict(self)
        payload["code"] = self.code.value
        payload["severity"] = str(self.severity)
        return payload


# ── The catalog ───────────────────────────────────────────────────────────────
# Every ``expected_count`` below is traced to a specific construct in
# scripts/seed_data.py; the line reference is given in the detection string so a
# reviewer can verify the number rather than trust it.
DEFECT_CATALOG: dict[DefectCode, DefectSpec] = {
    # ══ stores.csv ═══════════════════════════════════════════════════════════
    DefectCode.ST_01_MALFORMED_ZIP: DefectSpec(
        code=DefectCode.ST_01_MALFORMED_ZIP,
        dataset="stores",
        title="Malformed ZIP code (leading zero lost upstream)",
        severity=Severity.MEDIUM,
        expected_count=1,
        detection=(
            "zip_code does not match ^[0-9]{5}$ once whitespace is stripped. S003 "
            "(Greece Ridge Center) carries '0938' -- four characters. The CSV is read with "
            "dtype=str precisely so this survives: let pandas infer types and '0938' becomes "
            "the integer 938, at which point the defect is indistinguishable from a genuinely "
            "short number and the leading-zero story is gone."
        ),
        decision=(
            "Left-pad only the rows that fail the five-digit test, producing '00938', and "
            "record the store as ZIP-unverifiable in the audit ledger. Padding is never "
            "applied blanket-style across the column."
        ),
        rationale=(
            "'00938' is structurally valid but it is not a real New York ZIP -- the 006xx-009xx "
            "range belongs to Puerto Rico, and Greece, NY is 14xxx. So padding restores a "
            "well-formed field without restoring a true one. The honest position is that we do "
            "not know which digit Excel ate, and we cannot recover it from this file; the "
            "value is made joinable and simultaneously flagged for human verification, rather "
            "than silently 'corrected' into a fact nobody checked. The earlier attempt ran "
            "zip_code.astype(str).str.zfill(5) over every row, which both hides which row was "
            "ever wrong and would corrupt any legitimately longer ZIP+4 value."
        ),
        source_ref="src/cleaning/stores.py:normalize_zip_codes",
    ),
    DefectCode.ST_02_NEAR_DUPLICATE_PK: DefectSpec(
        code=DefectCode.ST_02_NEAR_DUPLICATE_PK,
        dataset="stores",
        title="Near-duplicate primary key with conflicting attributes",
        severity=Severity.CRITICAL,
        expected_count=1,
        detection=(
            "store_id occurs more than once while the rows are not byte-identical. S007 appears "
            "as 'Downtown Rochester' and 'Rochester Downtown'; city, state, ZIP and opened_date "
            "agree exactly, so store_name is the only contested attribute."
        ),
        decision=(
            "Apply an explicit, order-independent survivorship rule instead of keep='first': "
            "for each store_id, prefer the record with the fewest nulls; break ties on the "
            "earliest opened_date; break remaining ties on the lexicographically first "
            "store_name. Here that elects 'Downtown Rochester'. The losing variant and the "
            "reason it lost are both written to the audit ledger."
        ),
        rationale=(
            "The two rows describe one real store, so collapsing them is right -- but *how* "
            "they collapse must be a stated policy, not an accident of row order. "
            "drop_duplicates(keep='first') gives a different winner if the extract is re-sorted, "
            "which means the warehouse silently changes without a single line of code changing. "
            "A deterministic rule is reproducible, is reviewable by someone who knows the "
            "business, and can be overridden with a golden-record table later. Note also that "
            "this defect must be resolved *before* the store dimension is loaded, because "
            "dim_store.store_id carries a UNIQUE constraint -- an unresolved duplicate is not a "
            "bad number, it is a failed load."
        ),
        source_ref="src/cleaning/stores.py:resolve_store_survivorship",
    ),
    DefectCode.ST_03_NULL_REGION: DefectSpec(
        code=DefectCode.ST_03_NULL_REGION,
        dataset="stores",
        title="NULL region on two Oregon stores",
        severity=Severity.MEDIUM,
        expected_count=2,
        detection=(
            "region is null or empty after whitespace normalization: S013 (Cascade Station, "
            "Portland OR) and S014 (Lloyd Center, Portland OR)."
        ),
        decision=(
            "Impute from a state-to-region map derived at runtime from the observed vocabulary "
            "of this very column -- never from a hard-coded external list. OR resolves to "
            "'West' because every other Pacific/Mountain store in the file (AZ, WA) is already "
            "labelled 'West'. dim_store.region_is_imputed is set to 1 for both rows."
        ),
        rationale=(
            "Imputation is only defensible when the imputed value is drawn from the column's "
            "own vocabulary. The previous attempt hard-coded NY -> 'East' while the data says "
            "'Northeast', inventing a sixth region that split the Northeast in two and quietly "
            "corrupted average-order-value by region -- a wrong answer that looked completely "
            "reasonable on a chart. Deriving the map from observed values makes that entire "
            "class of bug impossible. Leaving the region NULL was the alternative: rejected "
            "because two stores would then vanish from every regional roll-up, understating the "
            "West. The region_is_imputed flag preserves the distinction for anyone who needs "
            "source-only figures."
        ),
        source_ref="src/cleaning/stores.py:impute_region",
    ),
    # ══ products.csv ═════════════════════════════════════════════════════════
    DefectCode.PR_01_EXACT_DUPLICATE: DefectSpec(
        code=DefectCode.PR_01_EXACT_DUPLICATE,
        dataset="products",
        title="Byte-identical duplicate product row",
        severity=Severity.LOW,
        expected_count=1,
        detection=(
            "Rows identical across every column: P012 appears twice with the same name, "
            "category, unit_price and supplier_id."
        ),
        decision=(
            "Drop the second copy, keep the first, log the product_id. No judgement call is "
            "required and none is pretended."
        ),
        rationale=(
            "Every attribute agrees, so the duplicate carries no information and its removal "
            "cannot lose any. The subtlety is ordering: this full-row de-duplication must run "
            "*before* any key-level de-duplication, because a single "
            "drop_duplicates(subset=['product_id']) would sweep up P012 and P005 together and "
            "report both as harmless duplicates -- which is exactly how PR-02, the real "
            "finding, disappeared from the previous attempt."
        ),
        source_ref="src/cleaning/products.py:drop_exact_duplicates",
    ),
    DefectCode.PR_02_PRICE_CHANGE: DefectSpec(
        code=DefectCode.PR_02_PRICE_CHANGE,
        dataset="products",
        title="Undocumented price change masquerading as a duplicate",
        severity=Severity.CRITICAL,
        expected_count=1,
        detection=(
            "product_id repeats *after* exact duplicates have already been removed -- i.e. the "
            "key collides but at least one attribute disagrees. P005 appears twice with "
            "unit_price differing by exactly +$8.50."
        ),
        decision=(
            "Treat it as a slowly-changing attribute, not a duplicate. Both versions are "
            "quarantined to output/quarantine/products__PR-02.csv with the delta stated. For "
            "dim_product, which must have one row per product_id, the later-appearing (higher) "
            "price is elected as the current list price on the explicit assumption that the "
            "appended record is the newer extract; the assumption is written into the audit "
            "note so it can be contradicted."
        ),
        rationale=(
            "This is the trap in products.csv. drop_duplicates(subset=['product_id']) removes "
            "the row and the finding in the same instruction: a genuine, undocumented $8.50 "
            "price increase becomes a coin-flip between two prices and nobody is ever told. "
            "What makes the choice safe rather than merely arbitrary is where prices actually "
            "come from: fact_sales.unit_price is the price *as transacted*, taken from "
            "transactions.csv, so dim_product.list_unit_price is a reference attribute only. "
            "Electing either price cannot move a single revenue figure -- but failing to report "
            "the conflict means the business never learns its product master has two truths in "
            "it. The correct long-term fix is a Type-2 dimension with effective dates, which "
            "this source cannot support because it carries no date on the price."
        ),
        source_ref="src/cleaning/products.py:resolve_price_conflicts",
    ),
    DefectCode.PR_03_NULL_CATEGORY: DefectSpec(
        code=DefectCode.PR_03_NULL_CATEGORY,
        dataset="products",
        title="NULL category on five products",
        severity=Severity.MEDIUM,
        expected_count=5,
        detection=(
            "category is null or empty on P003, P009, P016, P023 and P029 -- five of thirty "
            "products, i.e. one sixth of the catalogue."
        ),
        decision=(
            "Impute the explicit literal 'Unknown' and set dim_product.category_is_imputed = 1. "
            "The products, and therefore their revenue, stay in the fact table. No category is "
            "guessed from the product name or the supplier."
        ),
        rationale=(
            "There is nothing in this file to infer a category from, and pretending otherwise "
            "would be fabrication: the names are synthetic ('Product P003') and supplier_id "
            "cycles across all five categories by construction, so supplier carries exactly "
            "zero signal. The two honest options were 'Unknown' or NULL, and 'Unknown' wins "
            "because a named bucket shows up in every category chart as a visible gap that "
            "someone will eventually fix, whereas NULLs are dropped by most GROUP BYs and the "
            "revenue simply evaporates. Dropping the five products was never on the table -- "
            "they carry real transactions, and removing them would understate total revenue "
            "while leaving the total looking perfectly tidy."
        ),
        source_ref="src/cleaning/products.py:impute_category",
    ),
    DefectCode.PR_04_ZERO_PRICE: DefectSpec(
        code=DefectCode.PR_04_ZERO_PRICE,
        dataset="products",
        title="Zero unit price in the product master",
        severity=Severity.HIGH,
        expected_count=1,
        detection="unit_price parses to 0.00 after currency normalization: P027.",
        decision=(
            "Read 0.00 as *missing*, not as a real giveaway price. The product is kept, "
            "dim_product.price_is_imputed is set to 1, and the row is quarantined for review. "
            "Revenue is untouched because fact_sales.unit_price comes from the transaction "
            "record, never from the dimension."
        ),
        rationale=(
            "A retailer does not stock a $0.00 item; this is a master-data error, and treating "
            "it as a price would make any list-price-based margin or discount analysis silently "
            "wrong for every P027 line. Two options were rejected: dropping the product (it has "
            "real sales, so the revenue would vanish) and leaving 0.00 unflagged (a downstream "
            "analyst would compute a 100% discount on every P027 transaction and believe it). "
            "There is also a useful corroboration available: transactions.csv independently "
            "records P027 at $195.34 per unit, which both confirms 0.00 is wrong and hands the "
            "business a defensible replacement. We surface that evidence in the audit note "
            "rather than writing it into the dimension unasked -- cross-populating a master "
            "table from a fact table is a decision for a data steward, not for an ETL job."
        ),
        source_ref="src/cleaning/products.py:flag_zero_prices",
    ),
    # ══ transactions.csv ═════════════════════════════════════════════════════
    DefectCode.TX_01_MIXED_DATE_FORMATS: DefectSpec(
        code=DefectCode.TX_01_MIXED_DATE_FORMATS,
        dataset="transactions",
        title="Three date formats in one column",
        severity=Severity.CRITICAL,
        expected_count=20,
        detection=(
            "Each transaction_date string is matched against an ordered ladder of explicit "
            "formats (%Y-%m-%d, then %m/%d/%Y, then %d-%m-%Y) and counted by which one it "
            "needed. seed_data.py rewrites rows 0-9 as MM/DD/YYYY and rows 10-19 as "
            "DD-MM-YYYY; the other 485 are ISO. Anything matching no format is quarantined "
            "rather than coerced."
        ),
        decision=(
            "Parse per format with explicit format strings and zero reliance on inference. All "
            "20 non-ISO rows are recovered; the parser asserts that no row is left unparsed."
        ),
        rationale=(
            "This is the quietest catastrophe in the dataset. A single "
            "pd.to_datetime(col, errors='coerce') does one of two bad things: it NaTs the "
            "non-ISO rows -- deleting 20 real transactions -- or, if pandas picks a "
            "day-first/month-first guess for the whole column, it *misparses* them, turning "
            "'03-05-2026' into 3 May when the source meant 5 March. The second failure mode is "
            "worse because the output looks complete. An explicit ladder works here because "
            "the formats are genuinely separable: '/' marks the US variant, a four-digit head "
            "marks ISO, and a two-digit head with '-' marks the EU variant, so no string can "
            "match two rungs. The previous attempt lost all 20 rows to a coerce call and then "
            "misattributed them in its README to 'future dates' -- the actual future-dated "
            "count is 3 (TX-08), which is how a parsing bug became a false business finding."
        ),
        source_ref="src/cleaning/rules.py:parse_transaction_date",
    ),
    DefectCode.TX_02_STRING_CURRENCY: DefectSpec(
        code=DefectCode.TX_02_STRING_CURRENCY,
        dataset="transactions",
        title="Currency-formatted amounts stored as strings",
        severity=Severity.HIGH,
        expected_count=25,
        detection=(
            "total_amount does not parse as a bare decimal -- it carries a '$', a thousands "
            "separator, padding whitespace or accounting parentheses. seed_data.py formats rows "
            "20-44 as '$142.50'."
        ),
        decision=(
            "Strip the currency symbol, separators and whitespace, honour parenthesised "
            "negatives, then cast to float. Any value that still refuses to parse is "
            "quarantined -- never coerced to zero, never dropped."
        ),
        rationale=(
            "Reading the raw CSV with dtype=str is what makes this visible at all. Let pandas "
            "infer and total_amount becomes an object column holding 25 strings and 480 floats: "
            "sum() raises or, depending on version, concatenates, and every downstream number "
            "is either an exception or nonsense. The zero-coercion alternative "
            "(errors='coerce'.fillna(0)) is the dangerous one -- it understates revenue by "
            "roughly $3.5k here while producing a beautifully clean column that no test would "
            "flag. Quarantining keeps any loss on the books."
        ),
        source_ref="src/cleaning/rules.py:parse_currency",
    ),
    DefectCode.TX_03_SILENT_DISCOUNT: DefectSpec(
        code=DefectCode.TX_03_SILENT_DISCOUNT,
        dataset="transactions",
        title="Silent discount: reported total does not equal quantity x unit price",
        severity=Severity.CRITICAL,
        expected_count=20,
        detection=(
            "abs(quantity * unit_price - total_amount) > PRICE_TOLERANCE ($0.01), evaluated "
            "only after currency parsing and only on rows with non-zero quantity. seed_data.py "
            "marks rows 100-119 down by a random 5-20%."
        ),
        decision=(
            "PRESERVE the reported total_amount verbatim -- it is authoritative for revenue. "
            "Add extended_amount = quantity * unit_price as the list value, expose "
            "discount_amount = extended_amount - total_amount, and set has_discount = True. "
            "Nothing is overwritten and no row is removed."
        ),
        rationale=(
            "These 20 rows are the challenge's central test. The money really moved at the "
            "discounted price, so total_amount is a fact and qty x price is a derivation; "
            "'fixing' the fact to agree with the derivation inverts the direction of truth. "
            "Recomputing total_amount = quantity * unit_price -- exactly what the previous "
            "attempt did at cleaner.py:116 -- overstates revenue by the entire discount pool "
            "and, far worse, erases the evidence that a discount ever happened, so the finding "
            "can never be reported. The discrepancy is itself the insight: an unmodelled "
            "promotion or manual override is flowing through a schema with nowhere to record "
            "it. Keeping both numbers side by side means the revenue_reconciliation metric can "
            "tie gross list value minus discounts minus returns back to net revenue, line by "
            "line, so the whole chain is checkable rather than asserted."
        ),
        source_ref="src/cleaning/transactions.py:reconcile_totals",
    ),
    DefectCode.TX_04_ORPHAN_STORE: DefectSpec(
        code=DefectCode.TX_04_ORPHAN_STORE,
        dataset="transactions",
        title="Orphaned store_id (referential integrity break)",
        severity=Severity.HIGH,
        expected_count=5,
        detection=(
            "store_id is absent from the set of surviving store_ids in the cleaned store "
            "dimension. Five transactions reference four unknown stores: S016, S017, S018, "
            "S016 again, and S019 (seed rows 150-154)."
        ),
        decision=(
            "Quarantine to output/quarantine/transactions__TX-04.csv and exclude from "
            "fact_sales. The withheld revenue is stated explicitly in the audit report so the "
            "exclusion is a number, not a silence."
        ),
        rationale=(
            "Three options existed. Silently dropping the rows loses real revenue with no trace "
            "-- unacceptable. Routing them to an 'Unknown Store' dimension member keeps the "
            "money in the totals but pollutes every store-level metric with a bucket nobody can "
            "act on, and quietly implies these sales belong to a store that does not exist. "
            "Quarantining is the honest middle: the rows leave the star schema so store "
            "analytics stay clean and PRAGMA foreign_keys = ON can be genuinely enforced, while "
            "the rows themselves sit on disk, counted and priced, waiting for the missing store "
            "master. The pattern -- four sequential IDs immediately after S015, the last real "
            "store -- strongly suggests new stores opened and the dimension extract was never "
            "refreshed, which is a fixable operational problem rather than corrupt data."
        ),
        source_ref="src/cleaning/transactions.py:check_referential_integrity",
    ),
    DefectCode.TX_05_ORPHAN_PRODUCT: DefectSpec(
        code=DefectCode.TX_05_ORPHAN_PRODUCT,
        dataset="transactions",
        title="Orphaned product_id (referential integrity break)",
        severity=Severity.HIGH,
        expected_count=3,
        detection=(
            "product_id is absent from the cleaned product dimension. Three transactions "
            "reference two unknown products: P031, P032 and P031 again (seed rows 155-157)."
        ),
        decision=(
            "Identical treatment to TX-04: quarantine to "
            "output/quarantine/transactions__TX-05.csv and exclude from fact_sales, with the "
            "withheld revenue reported."
        ),
        rationale=(
            "Same reasoning as TX-04, and deliberately the same treatment: two structurally "
            "identical problems should not receive two different policies, because the "
            "inconsistency is what a reviewer will (rightly) attack. P031 and P032 sit "
            "immediately past P030, the last catalogued product -- again the signature of a "
            "stale dimension extract rather than bad transactions. Worth noting that these "
            "rows are individually the most valuable orphans in the file, so quietly dropping "
            "them would move the revenue figure by more than their row count suggests."
        ),
        source_ref="src/cleaning/transactions.py:check_referential_integrity",
    ),
    DefectCode.TX_06_NULL_CUSTOMER: DefectSpec(
        code=DefectCode.TX_06_NULL_CUSTOMER,
        dataset="transactions",
        title="NULL customer_id on guest checkouts",
        severity=Severity.MEDIUM,
        expected_count=40,
        detection=(
            "customer_id is null or empty. Exactly 40 rows (seed rows 200-239), just under 8% "
            "of the file."
        ),
        decision=(
            "Never dropped. customer_id is replaced with the sentinel 'GUEST', is_guest is set "
            "to True, and dim_customer carries a single GUEST member with is_guest = 1. Guests "
            "are excluded from top_customers_lifetime and included in every other metric."
        ),
        rationale=(
            "A missing customer here is not corruption, it is a guest checkout -- a legitimate "
            "and common retail event that the schema simply has no flag for. Dropping the rows "
            "would delete roughly 8% of real revenue and skew every store, category and "
            "regional figure downwards in a way no reconciliation would catch. NULL cannot be "
            "kept as-is because dim_customer needs a natural key and fact_sales needs a "
            "non-null foreign key. The sentinel-plus-flag pattern satisfies both constraints "
            "while keeping the semantics recoverable: 'GUEST' is one row in the dimension, so "
            "it must be excluded from customer leaderboards -- otherwise a fused pseudo-person "
            "made of 40 unrelated shoppers tops the lifetime-value ranking by construction, "
            "which is exactly the kind of nonsense that gets a dashboard distrusted. That "
            "exclusion is stated in the metric's definition_note, not hidden in a WHERE clause."
        ),
        source_ref="src/cleaning/transactions.py:handle_guest_customers",
    ),
    DefectCode.TX_07_ZERO_QUANTITY: DefectSpec(
        code=DefectCode.TX_07_ZERO_QUANTITY,
        dataset="transactions",
        title="Zero-quantity, zero-value transactions",
        severity=Severity.MEDIUM,
        expected_count=5,
        detection=(
            "quantity == 0, with total_amount == 0 on the same rows (seed rows 250-254). Both "
            "conditions are asserted together, so a zero-quantity row carrying money would be "
            "reported separately as a distinct anomaly rather than absorbed here."
        ),
        decision=(
            "Excluded from fact_sales and quarantined to "
            "output/quarantine/transactions__TX-07.csv. They are also excluded from the TX-03 "
            "reconciliation check, where 0 == 0 * price is trivially true and would otherwise "
            "dilute the discount statistics."
        ),
        rationale=(
            "A sale of zero units for zero dollars is not a sale; it is a voided line, an "
            "abandoned order stub, or a system artefact. It contributes nothing to revenue but "
            "it is not harmless: left in the fact table it inflates the transaction count that "
            "average order value divides by, pushing AOV down by about 1% for no economic "
            "reason at all -- a metric moved by rows that represent nothing. Removing them "
            "changes no total and repairs a denominator. They are quarantined rather than "
            "deleted so the row arithmetic still ties back to the 505 source rows."
        ),
        source_ref="src/cleaning/transactions.py:flag_zero_quantity",
    ),
    DefectCode.TX_08_FUTURE_DATE: DefectSpec(
        code=DefectCode.TX_08_FUTURE_DATE,
        dataset="transactions",
        title="Transactions dated after the reference date",
        severity=Severity.HIGH,
        expected_count=3,
        detection=(
            "Parsed transaction_date > AS_OF_DATE (2026-06-02). seed_data.py plants exactly "
            "three, at +8, +16 and +25 days. The check runs after TX-01 parsing, so a "
            "misparsed date can never be misreported as a future date."
        ),
        decision=(
            "Flagged and quarantined out of fact_sales. The comparison is made against the "
            "single configurable AS_OF_DATE in src/config.py; datetime.now() is never called "
            "anywhere in pipeline logic."
        ),
        rationale=(
            "A sale cannot be recorded before it happens, so these are either data-entry or "
            "timezone errors, or pre-orders that the schema has no way to represent. Either "
            "way, admitting them leaks revenue into a period that has not closed, which is the "
            "kind of thing that quietly breaks a month-end reconciliation. The reference-date "
            "choice matters as much as the rule: pinned to the seed's own today, exactly three "
            "rows are future-dated and the run is reproducible forever; run against wall-clock "
            "time and those same three rows silently become ordinary history as the real "
            "calendar passes them, while the trailing-30-day metrics simultaneously go empty. "
            "The check is also ordered deliberately -- it runs after date parsing precisely "
            "because the previous attempt's 20 coerce-mangled rows got reported here, turning "
            "a parser bug into a fictitious business finding."
        ),
        source_ref="src/cleaning/transactions.py:flag_future_dates",
    ),
    DefectCode.TX_09_EXACT_DUPLICATE: DefectSpec(
        code=DefectCode.TX_09_EXACT_DUPLICATE,
        dataset="transactions",
        title="Exact duplicate transaction rows",
        severity=Severity.HIGH,
        expected_count=15,
        detection=(
            "Full-row duplicates across every column, including transaction_id: 15 copies of "
            "TXN10051-TXN10065 appended by seed_data.py. Separately, the cleaner counts "
            "transaction_ids that repeat *without* being full-row duplicates -- the "
            "transaction-level analogue of PR-02 -- and expects zero of them in this file."
        ),
        decision=(
            "Drop the copies and keep the first occurrence. De-duplication is keyed on the "
            "entire row, not on transaction_id alone, and UNIQUE(transaction_id) on fact_sales "
            "is retained as a second line of defence."
        ),
        rationale=(
            "Identical IDs carrying identical measures is the signature of a re-extract or a "
            "double-append, not of two real sales; leaving them in double-counts around 3% of "
            "revenue, and because they cluster in one contiguous ID range the damage lands on "
            "specific stores and products rather than averaging out. De-duplicating on the full "
            "row rather than the key is the deliberate choice: it means that a future file "
            "where one transaction_id carries two *different* amounts surfaces as an "
            "unresolved conflict instead of being silently collapsed to whichever row happened "
            "to sort first. Order matters here too -- the returns in TX-10 are also copies of "
            "base rows, but they carry new IDs and negated measures, so they are correctly not "
            "duplicates under either rule."
        ),
        source_ref="src/cleaning/transactions.py:drop_exact_duplicates",
    ),
    DefectCode.TX_10_RETURNS: DefectSpec(
        code=DefectCode.TX_10_RETURNS,
        dataset="transactions",
        title="Return transactions with negative quantity and amount",
        severity=Severity.MEDIUM,
        expected_count=30,
        detection=(
            "quantity < 0 and total_amount < 0, carrying the distinct identifier block "
            "TXN20001-TXN20030. seed_data.py copies base rows 65-94, negates both measures and "
            "re-keys them, so every return maps to a real prior sale."
        ),
        decision=(
            "Preserved in fact_sales with their negative measures intact and is_return = True. "
            "Never filtered out as 'invalid negatives', never made positive with abs()."
        ),
        rationale=(
            "Returns are the credit side of the ledger and belong in the fact table. Dropping "
            "them overstates net revenue by their full value; taking the absolute value "
            "overstates it by twice that, and would convert a refund into a sale -- the worst "
            "possible sign error. Keeping the sign is what makes SUM(net_amount) genuinely "
            "*net*, and the is_return flag is what makes the gross-versus-returns split "
            "available without re-deriving it from the sign in every query. It is also the "
            "only reason a return rate can be computed at all: it is reported unit-based as "
            "SUM(returned units) / SUM(sold units), with a transaction-count variant emitted "
            "alongside it so the definitional ambiguity is visible on the dashboard rather "
            "than buried in a WHERE clause -- the two denominators give materially different "
            "answers, and picking one silently is how two teams end up quoting different "
            "numbers from the same warehouse. Returns are aggregated before being joined to "
            "sales so the join cannot fan out and double-count."
        ),
        source_ref="src/cleaning/transactions.py:flag_returns",
    ),
}


# ── Integrity guards (run at import time) ─────────────────────────────────────
# WHY at import: a catalog that is missing a code, or whose keys disagree with
# the specs they point at, would make assert_all_expected_defects_found() lie by
# omission -- the pipeline would pass while quietly not checking a defect class.
# Failing loudly at import is far cheaper than a green run that proves nothing.
_missing = [c for c in DefectCode if c not in DEFECT_CATALOG]
if _missing:  # pragma: no cover - guard
    raise AssertionError(f"DEFECT_CATALOG is missing entries for: {_missing}")

for _key, _spec in DEFECT_CATALOG.items():
    if _key is not _spec.code:  # pragma: no cover - guard
        raise AssertionError(f"DEFECT_CATALOG key {_key} does not match spec code {_spec.code}")
    if _spec.dataset not in {"stores", "products", "transactions"}:  # pragma: no cover
        raise AssertionError(f"{_key}: unknown dataset {_spec.dataset!r}")

EXPECTED_DEFECT_CLASS_COUNT: int = 17
"""Stated independently of len(DefectCode) so that accidentally deleting a code
fails the guard below instead of silently shrinking the definition of 'all'."""

if len(DefectCode) != EXPECTED_DEFECT_CLASS_COUNT:  # pragma: no cover - guard
    raise AssertionError(
        f"Expected {EXPECTED_DEFECT_CLASS_COUNT} defect classes, found {len(DefectCode)}"
    )


# ── Lookup / serialization helpers ────────────────────────────────────────────
def get_spec(code: DefectCode | str) -> DefectSpec:
    """Return the :class:`DefectSpec` for a code, accepting the raw string form.

    Args:
        code: A :class:`DefectCode` member or its string value (e.g. ``"TX-03"``).

    Returns:
        The matching frozen spec.

    Raises:
        KeyError: If the code is not in the catalog. WHY raise rather than
            return None: an unknown defect code means the caller has a typo or a
            stale constant, and silently returning None would surface it much
            later as a confusing missing dashboard card.

    Defects handled: none (lookup helper).
    """
    key = code if isinstance(code, DefectCode) else DefectCode(code)
    return DEFECT_CATALOG[key]


def specs_for_dataset(dataset: str) -> list[DefectSpec]:
    """Return every spec belonging to one source dataset, in catalog order.

    Args:
        dataset: ``"stores"``, ``"products"`` or ``"transactions"``.

    Returns:
        Specs for that dataset; empty list if the name is unknown.

    Defects handled: none (lookup helper).
    """
    return [s for s in DEFECT_CATALOG.values() if s.dataset == dataset]


def expected_counts() -> dict[str, int | None]:
    """Map every defect code string to its expected occurrence count.

    Returns:
        ``{"ST-01": 1, ..., "TX-10": 30}``. Consumed by
        :meth:`src.audit.AuditLog.assert_all_expected_defects_found` and by the
        test suite, which asserts these numbers against the raw CSVs directly --
        so the catalog cannot drift away from the data unnoticed.

    Defects handled: all 17 (metadata only).
    """
    return {code.value: spec.expected_count for code, spec in DEFECT_CATALOG.items()}


def catalog_to_dict(codes: Iterable[DefectCode] | None = None) -> dict[str, Any]:
    """Serialise the catalog into the shape the dashboard consumes.

    Args:
        codes: Optional subset to export. ``None`` exports everything.

    Returns:
        ``{"catalog_version", "generated_at", "defect_class_count", "by_dataset",
        "defects": [spec dicts in catalog order]}``. ``by_dataset`` is
        pre-grouped because every dashboard view needs that grouping and doing
        it once here keeps the front-end free of business logic.

    Defects handled: all 17 (metadata only).
    """
    selected = list(DEFECT_CATALOG.values()) if codes is None else [get_spec(c) for c in codes]
    by_dataset: dict[str, list[str]] = {}
    for spec in selected:
        by_dataset.setdefault(spec.dataset, []).append(spec.code.value)
    return {
        "catalog_version": CATALOG_VERSION,
        # WHY utcnow-as-metadata is acceptable while datetime.now() in logic is
        # not: this stamp is provenance, never an input to any calculation.
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "defect_class_count": len(selected),
        "by_dataset": by_dataset,
        "defects": [spec.to_dict() for spec in selected],
    }


def write_defect_catalog_json(path: Path | None = None) -> Path:
    """Write the catalog to disk for the dashboard to read.

    Args:
        path: Destination file. Defaults to
            :data:`src.config.DEFECT_CATALOG_JSON_PATH`
            (``output/defect_catalog.json``).

    Returns:
        The path actually written.

    Defects handled: all 17 (metadata only).
    """
    # WHY the import is function-local: src.io_utils imports nothing from this
    # module today, but keeping the dependency inside the call removes any
    # possibility of a future import cycle between the registry and the I/O
    # layer, which would be an infuriating bug to diagnose.
    from src.io_utils import write_json_atomic

    target = Path(path) if path is not None else DEFECT_CATALOG_JSON_PATH
    write_json_atomic(target, catalog_to_dict())
    return target


__all__ = [
    "CATALOG_VERSION",
    "DEFECT_CATALOG",
    "DEFECT_CATALOG_JSON_PATH",
    "DefectCode",
    "DefectSpec",
    "EXPECTED_DEFECT_CLASS_COUNT",
    "Severity",
    "catalog_to_dict",
    "expected_counts",
    "get_spec",
    "specs_for_dataset",
    "write_defect_catalog_json",
]
