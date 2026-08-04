"""Transactional loader: cleaned DataFrames -> ``output/warehouse.db`` star schema.

What this module does
---------------------
Takes the three cleaned frames produced by ``src/cleaning/*`` and materialises the
star schema declared in :file:`schema.sql`. It is the only writer of the warehouse
file, and it enforces four guarantees that a naive ``df.to_sql()`` cannot:

1. **All-or-nothing.** Drop, create, load dimensions, load the fact, verify, commit
   — one SQLite transaction. Any exception rolls back and the previous
   ``warehouse.db`` is left untouched. A half-loaded warehouse is worse than no
   warehouse, because it looks like a working one.
2. **Referential integrity is enforced by the database, not asserted by the
   loader.** ``PRAGMA foreign_keys = ON`` is set and then *proved* at runtime with
   a probe insert that must fail (:func:`probe_foreign_key_enforcement`).
3. **Zero tolerance for unresolved natural keys.** Every fact row must join to a
   real dimension member. An orphan reaching this layer is a bug in the cleaning
   layer, so it raises with the offending keys named rather than being absorbed by
   a ``-1 / Unknown`` member.
4. **Determinism.** Surrogate keys are assigned from a natural-key sort, and the
   database is built into a scratch file that is atomically moved into place. Two
   runs over the same input produce byte-identical files.

Defect codes owned: none — the cleaning layer owns all 17. This module is the
*second, independent* enforcement of the referential decisions behind TX-04 and
TX-05: even if a cleaner let an orphan through, the FK constraints and the
key-resolution assertion here refuse to load it. It also carries TX-03's decision
into storage unchanged (``net_amount`` is the reported total, never recomputed).

Inputs:  ``stores``, ``products``, ``transactions`` DataFrames (contract §4/§7b
         column lists), a destination path, and the reference date.
Outputs: ``output/warehouse.db`` plus a ``dict[str, int]`` of row counts and
         verification results.
"""

from __future__ import annotations

import calendar
import datetime as dt
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from src.audit import AuditLog
from src.config import AS_OF_DATE, DB_PATH, GUEST_CUSTOMER_ID, PRICE_TOLERANCE
from src.defects import DefectCode

# ── Where the DDL lives ───────────────────────────────────────────────────────
# WHY resolve from __file__ rather than importing config.SCHEMA_SQL_PATH: they
# point at the same file, but deriving it locally means this module still works
# when it is copied, vendored, or imported from a test fixture directory.
SCHEMA_PATH: Path = Path(__file__).with_name("schema.sql")

# ── Column contracts (contract §4 + §7b addendum) ─────────────────────────────
# WHY these are named constants and validated up front: the cleaning modules are
# written by other agents in parallel. If one of them renames a column, the
# failure should be a single message naming the module and the missing columns —
# not a KeyError 200 lines later, or worse, a NULL that quietly loads.
REQUIRED_STORE_COLUMNS: tuple[str, ...] = (
    "store_id", "store_name", "city", "state", "zip_code", "zip_is_suspect",
    "region", "region_is_imputed", "opened_date",
)
REQUIRED_PRODUCT_COLUMNS: tuple[str, ...] = (
    "product_id", "product_name", "category", "category_is_imputed",
    "list_unit_price", "price_is_imputed", "price_conflict", "supplier_id",
)
REQUIRED_TRANSACTION_COLUMNS: tuple[str, ...] = (
    "transaction_id", "transaction_date", "store_id", "product_id", "customer_id",
    "is_guest", "quantity", "unit_price", "extended_amount", "total_amount",
    "discount_amount", "has_discount", "is_return",
)

# ── Insert column order, mirroring schema.sql exactly ─────────────────────────
# WHY spell the columns out instead of "INSERT INTO t VALUES (?,?,...)": a
# positional insert binds silently to whatever order the CREATE TABLE happens to
# have, so adding a column to the schema would shift every value one place to the
# left and load a warehouse full of plausible-looking nonsense.
DIM_DATE_COLUMNS: tuple[str, ...] = (
    "date_key", "full_date", "year", "quarter", "month", "year_month",
    "month_name", "day_of_month", "day_of_week", "is_weekend",
)
DIM_STORE_COLUMNS: tuple[str, ...] = (
    "store_key", "store_id", "store_name", "city", "state", "zip_code",
    "zip_is_suspect", "region", "region_is_imputed", "opened_date",
)
DIM_PRODUCT_COLUMNS: tuple[str, ...] = (
    "product_key", "product_id", "product_name", "category", "category_is_imputed",
    "list_unit_price", "price_is_imputed", "price_conflict", "supplier_id",
)
DIM_CUSTOMER_COLUMNS: tuple[str, ...] = ("customer_key", "customer_id", "is_guest")
FACT_SALES_COLUMNS: tuple[str, ...] = (
    "sales_key", "transaction_id", "date_key", "store_key", "product_key",
    "customer_key", "quantity", "unit_price", "extended_amount",
    "discount_amount", "net_amount", "is_return",
)

TABLE_LOAD_ORDER: tuple[str, ...] = (
    "dim_date", "dim_store", "dim_product", "dim_customer", "fact_sales",
)
"""Parents before child. WHY it is a hard requirement and not a preference: with
``PRAGMA foreign_keys = ON`` and immediate (non-deferred) constraints, inserting a
fact row before its dimension row exists fails on the spot."""

MONEY_DECIMALS: int = 2
"""Money is rounded to cents at the storage boundary. WHY here and not earlier:
the cleaning layer must preserve the source's own precision so reconciliation is
provable; the warehouse is where a single, stated rounding convention is applied so
that SUM() over the fact ties to the source to the cent."""


# ── Exceptions ────────────────────────────────────────────────────────────────
class WarehouseLoadError(RuntimeError):
    """Raised when the warehouse cannot be built correctly.

    WHY a dedicated type rather than a bare ``RuntimeError``: ``src/pipeline.py``
    treats any exception from this stage as fatal (exit code 2), and the test
    suite needs to assert that a *specific* failure mode fired — an unresolved
    foreign key, not an unrelated crash that happens to produce a traceback.
    """


class UnresolvedKeyError(WarehouseLoadError):
    """A fact row referenced a natural key that has no dimension member.

    This is deliberately fatal. The alternative — substituting a ``-1``/"Unknown"
    dimension member — is the single most common way a warehouse acquires
    permanent, invisible data loss: the orphaned revenue lands in a bucket that
    looks like a real category, someone charts it, and the underlying cleaning bug
    is never found. TX-04 and TX-05 are handled (quarantined, counted, written to
    ``output/quarantine/``) in the cleaning layer where they can be audited. If one
    reaches this far, the contract has been broken and the run must stop.

    Attributes:
        dimension: Which dimension failed to resolve (e.g. ``"dim_store"``).
        natural_column: The join column (e.g. ``"store_id"``).
        offending_keys: The distinct unresolved keys, sorted, capped for display.
        row_count: How many fact rows were affected.
    """

    def __init__(
        self,
        dimension: str,
        natural_column: str,
        offending_keys: Sequence[str],
        row_count: int,
    ) -> None:
        self.dimension = dimension
        self.natural_column = natural_column
        self.offending_keys = list(offending_keys)
        self.row_count = row_count
        shown = ", ".join(self.offending_keys[:20])
        more = "" if len(self.offending_keys) <= 20 else f" (+{len(self.offending_keys) - 20} more)"
        super().__init__(
            f"{row_count} fact row(s) reference {natural_column} value(s) absent from "
            f"{dimension}: [{shown}]{more}. This is a CLEANING-LAYER bug, not a load "
            f"problem: src/cleaning/transactions.py is contractually required to "
            f"quarantine orphan references (TX-04 / TX-05) before the warehouse sees "
            f"them. Refusing to load against an 'Unknown' member — that would hide the "
            f"defect inside a legitimate-looking dimension row."
        )


# ── Verification result ───────────────────────────────────────────────────────
@dataclass
class LoadVerification:
    """Post-load evidence that the warehouse is correct, not merely populated.

    Attributes:
        row_counts: ``{table: COUNT(*)}`` read back out of the database.
        fk_violations: Rows returned by ``PRAGMA foreign_key_check`` — must be 0.
        fk_enforcement_proven: True when a deliberately invalid insert was
            *rejected* by SQLite, proving ``PRAGMA foreign_keys = ON`` actually
            took effect (setting the pragma inside a transaction is a silent no-op,
            which is exactly the kind of thing that must be tested, not assumed).
        source_revenue: ``SUM(total_amount)`` over the cleaned transactions.
        warehouse_revenue: ``SUM(net_amount)`` over ``fact_sales``.
        revenue_delta: warehouse minus source. Must be zero to the cent.
        source_rows: Cleaned transaction row count, for the fact-completeness check.
        problems: Human-readable failures. Empty means the load is provably good.
    """

    row_counts: dict[str, int] = field(default_factory=dict)
    fk_violations: int = 0
    fk_enforcement_proven: bool = False
    source_revenue: float = 0.0
    warehouse_revenue: float = 0.0
    revenue_delta: float = 0.0
    source_rows: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was found wrong."""
        return not self.problems

    @property
    def revenue_delta_cents(self) -> int:
        """The tie-out gap in whole cents — 0 on a correct load."""
        return int(round(self.revenue_delta * 100))


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 · Input validation and value coercion
# ══════════════════════════════════════════════════════════════════════════════
def _require_columns(df: pd.DataFrame, required: Iterable[str], source: str) -> None:
    """Fail fast, and informatively, when an upstream frame is missing columns.

    Args:
        df: The frame handed to the loader.
        required: Column names the contract guarantees.
        source: Which cleaning module produces this frame — named in the error so
            the reader does not have to work out whose bug it is.

    Raises:
        WarehouseLoadError: Listing every missing column at once. WHY all at once
            rather than the first: a rename usually breaks several columns, and
            fixing them one crash at a time wastes the reviewer's afternoon.

    Defects handled: none (contract enforcement).
    """
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise WarehouseLoadError(
            f"{source} is missing contract-required column(s) {missing}. "
            f"Present columns: {list(df.columns)}. See CONTRACT.md §4/§7b — the "
            f"warehouse builds directly against those names."
        )


def _to_flag(series: pd.Series, column: str) -> pd.Series:
    """Coerce a boolean-ish column to a strict 0/1 integer series.

    Cleaned frames arrive with flags as ``bool``, ``numpy.bool_``, ``0/1`` ints, or
    (when they have been round-tripped through CSV) the strings ``"True"``/
    ``"False"``. SQLite would happily store all of those, producing a column that
    holds a mixture of ``1``, ``'True'`` and ``'true'`` — at which point
    ``WHERE is_return = 1`` silently returns a subset and every return-rate number
    is wrong in a way no error message will ever mention.

    Args:
        series: The column to coerce.
        column: Its name, for the error message.

    Returns:
        An ``int64`` series containing only 0 and 1.

    Raises:
        WarehouseLoadError: On NULLs or on any value that is not recognisably
            boolean. WHY raise instead of defaulting to 0: a NULL flag means the
            cleaner did not decide, and guessing "False" on its behalf invents a
            provenance claim ("this value was NOT imputed") that nobody made.

    Defects handled: none (type safety for the ST-01/ST-03/PR-02/PR-03/PR-04
        provenance flags and the TX-06/TX-10 semantic flags).
    """
    if series.isna().any():
        bad = int(series.isna().sum())
        raise WarehouseLoadError(
            f"Column {column!r} contains {bad} NULL flag value(s). A provenance flag "
            f"must be an explicit decision; NULL means no decision was recorded."
        )
    if series.dtype == bool or series.dtype == np.bool_:
        return series.astype("int64")

    truthy = {"1", "true", "t", "yes", "y"}
    falsy = {"0", "false", "f", "no", "n", "0.0"}

    def one(value: Any) -> int:
        if isinstance(value, (bool, np.bool_)):
            return int(value)
        if isinstance(value, (int, np.integer)) and int(value) in (0, 1):
            return int(value)
        # WHY accept floats: a flag that has been through a pandas merge with
        # missing rows becomes float64 (1.0/0.0) even when no NULL survives.
        if isinstance(value, (float, np.floating)) and float(value) in (0.0, 1.0):
            return int(value)
        text = str(value).strip().lower()
        if text in truthy:
            return 1
        if text in falsy:
            return 0
        raise WarehouseLoadError(
            f"Column {column!r} holds {value!r}, which is not a boolean. Flags must be "
            f"True/False, 1/0 or the strings 'True'/'False'."
        )

    return series.map(one).astype("int64")


def _to_iso_date(value: Any) -> str | None:
    """Normalise any date-ish value to an ISO-8601 ``'YYYY-MM-DD'`` string.

    WHY normalise at all when the values already look like dates: ``opened_date``
    may arrive as a ``str`` from the raw CSV or as a ``Timestamp`` if a cleaner
    parsed it. Storing both spellings in one TEXT column means ``date(x) = x`` in
    the schema's CHECK fails for half the rows, and any BETWEEN comparison compares
    ``'2010-03-15'`` with ``'2010-03-15 00:00:00'`` as text — which is False.

    Args:
        value: A string, ``datetime``, ``date``, ``Timestamp`` or NULL.

    Returns:
        The ISO date string, or ``None`` for NULL/NaT.

    Raises:
        WarehouseLoadError: If the value is not parseable as a date.

    Defects handled: none (storage normalisation).
    """
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    try:
        # WHY format-first: this is a normalisation helper, not a parser. TX-01's
        # ambiguous formats are resolved deliberately in src/cleaning/rules.py; if
        # something ambiguous reached here it must fail loudly, not be guessed at.
        return dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise WarehouseLoadError(
            f"Cannot store {value!r} as an ISO date. Dates must be parsed and "
            f"normalised in the cleaning layer (see TX-01), not inferred here."
        ) from exc


def _py(value: Any) -> Any:
    """Convert a numpy/pandas scalar to a plain Python value for sqlite3 binding.

    WHY this is mandatory rather than defensive: ``sqlite3`` refuses to bind
    ``numpy.int64`` and ``numpy.float64`` ("Error binding parameter — probably
    unsupported type"). Every value coming out of a DataFrame is one of those, so
    without this the very first ``executemany`` fails.

    Args:
        value: Any scalar.

    Returns:
        A ``str``/``int``/``float``/``None`` sqlite3 can bind.

    Defects handled: none (binding safety).
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value


def _records(df: pd.DataFrame, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """Turn a frame into sqlite3-bindable row tuples, in explicit column order.

    Args:
        df: Source frame.
        columns: The exact column order the INSERT statement uses.

    Returns:
        One tuple per row.

    Defects handled: none (binding helper).
    """
    subset = df[list(columns)]
    return [tuple(_py(v) for v in row) for row in subset.itertuples(index=False, name=None)]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 · Dimension construction (pure, deterministic, no I/O)
# ══════════════════════════════════════════════════════════════════════════════
#  WHY SURROGATE KEYS ARE ASSIGNED FROM A SORT, NOT FROM ARRIVAL ORDER
#  --------------------------------------------------------------------------
#  Every dimension below is sorted by its natural key before ``store_key = 1..N``
#  is stamped on. That single line is what makes the whole artifact diffable.
#
#  If keys were assigned in arrival order, they would depend on the order pandas
#  happened to produce rows — which depends on the raw CSV's row order (the
#  generator SHUFFLES it), on the survivorship rule's tie-breaking, and on
#  whichever merge the cleaner ran last. Two runs over identical input would then
#  produce a database in which S001 is store_key 7 the first time and 3 the second.
#  Consequences, in order of increasing annoyance:
#    * ``git diff`` / ``cmp`` on the built database is pure noise, so nobody checks.
#    * A saved query result or a dashboard snapshot referencing store_key 7 silently
#      points at a different store after the next load.
#    * A regression test that asserts on key values fails at random, gets marked
#      flaky, and stops being trusted.
#  Sorting costs microseconds and removes an entire class of "works on my machine".
# ══════════════════════════════════════════════════════════════════════════════
def build_dim_date(
    transactions: pd.DataFrame, as_of_date: dt.date | None = None
) -> pd.DataFrame:
    """Build a **dense** calendar dimension spanning the observed date range.

    Dense means every calendar day between the first and last transaction date
    gets a row, including days on which nothing was sold. See the extended note in
    :file:`schema.sql` — in short, a sparse date dimension makes an empty period
    disappear from a GROUP BY instead of appearing as a zero, so month-over-month
    growth silently compares non-adjacent months and reports a number that is not
    wrong so much as meaningless.

    The upper bound is extended to ``as_of_date`` when that is later than the last
    transaction. WHY: the trailing-30-day window in contract §6 is defined relative
    to ``AS_OF_DATE``. If the final days of that window have no ``dim_date`` rows,
    "no sales in the last two days" is indistinguishable from "those days do not
    exist", and any per-day average divides by the wrong denominator.

    Args:
        transactions: Cleaned transactions; only ``transaction_date`` is read.
        as_of_date: Reference "today" (:data:`src.config.AS_OF_DATE`). Never
            ``datetime.now()`` — contract §2.

    Returns:
        A frame with :data:`DIM_DATE_COLUMNS`, ordered by ``date_key``.

    Raises:
        WarehouseLoadError: If the fact feed is empty or holds unparsed dates.

    Defects handled: none directly. TX-08 (future dates) is quarantined upstream,
        which is precisely why this range ends at the last *legitimate*
        transaction rather than 25 days into a fictional future.
    """
    if transactions.empty:
        raise WarehouseLoadError(
            "Cannot build dim_date: the cleaned transaction feed is empty, so there "
            "is no observed date range. An empty fact feed means the cleaning layer "
            "rejected everything — investigate there, do not load an empty warehouse."
        )

    dates = pd.to_datetime(transactions["transaction_date"], errors="coerce")
    if dates.isna().any():
        # WHY name the transaction ids: "some dates failed to parse" is an
        # unactionable message. TX-01's whole lesson is that a coerce-to-NaT step
        # which does not say what it dropped is how 20 rows vanished last time.
        bad_ids = (
            transactions.loc[dates.isna(), "transaction_id"].astype(str).tolist()[:20]
        )
        raise WarehouseLoadError(
            f"{int(dates.isna().sum())} transaction(s) have an unparseable "
            f"transaction_date, e.g. {bad_ids}. Dates must be fully resolved in "
            f"src/cleaning/rules.py (TX-01) before the warehouse is built."
        )

    start: dt.date = dates.min().date()
    end: dt.date = dates.max().date()
    if as_of_date is not None and as_of_date > end:
        end = as_of_date

    rows: list[dict[str, Any]] = []
    for stamp in pd.date_range(start=start, end=end, freq="D"):
        day: dt.date = stamp.date()
        # WHY (weekday + 1) % 7: Python's Monday=0..Sunday=6 is remapped to
        # SQLite's strftime('%w') convention, Sunday=0..Saturday=6, so a reviewer
        # cross-checking this column against the database's own function agrees
        # with it instead of finding a silent off-by-one.
        dow = (day.weekday() + 1) % 7
        rows.append(
            {
                "date_key": int(day.strftime("%Y%m%d")),
                "full_date": day.isoformat(),
                "year": day.year,
                "quarter": (day.month - 1) // 3 + 1,
                "month": day.month,
                "year_month": f"{day.year:04d}-{day.month:02d}",
                "month_name": calendar.month_name[day.month],
                "day_of_month": day.day,
                "day_of_week": dow,
                "is_weekend": 1 if dow in (0, 6) else 0,
            }
        )
    # WHY sort even though date_range is already ordered: the sort is the
    # invariant. It survives someone later switching to a different generator.
    return pd.DataFrame(rows, columns=list(DIM_DATE_COLUMNS)).sort_values(
        "date_key", kind="mergesort", ignore_index=True
    )


def build_dim_store(stores: pd.DataFrame) -> pd.DataFrame:
    """Build the store dimension with deterministic surrogate keys.

    Args:
        stores: Cleaned stores frame (contract §7b column list).

    Returns:
        A frame with :data:`DIM_STORE_COLUMNS`, ordered by ``store_id``.

    Raises:
        WarehouseLoadError: On missing columns or a duplicated ``store_id``.

    Defects handled: ST-01 and ST-03 provenance flags are carried into storage
        (``zip_is_suspect``, ``region_is_imputed``); ST-02's resolution is verified
        here by the duplicate check, which is an independent second opinion on the
        survivorship rule.
    """
    _require_columns(stores, REQUIRED_STORE_COLUMNS, "clean_stores() output")
    frame = stores.copy()
    frame["store_id"] = frame["store_id"].astype(str)

    # WHY check here as well as relying on the UNIQUE constraint: the constraint
    # aborts the load with "UNIQUE constraint failed: dim_store.store_id", which
    # does not say WHICH store or that ST-02's survivorship rule is the suspect.
    duplicated = frame.loc[frame["store_id"].duplicated(keep=False), "store_id"]
    if not duplicated.empty:
        raise WarehouseLoadError(
            f"dim_store would contain duplicate store_id(s) {sorted(set(duplicated))}. "
            f"ST-02's survivorship rule in src/cleaning/stores.py must elect exactly "
            f"one row per store before the warehouse is built."
        )

    frame = frame.sort_values("store_id", kind="mergesort", ignore_index=True)
    frame["store_key"] = np.arange(1, len(frame) + 1, dtype="int64")
    frame["zip_is_suspect"] = _to_flag(frame["zip_is_suspect"], "stores.zip_is_suspect")
    frame["region_is_imputed"] = _to_flag(
        frame["region_is_imputed"], "stores.region_is_imputed"
    )
    frame["zip_code"] = frame["zip_code"].astype(str)
    frame["opened_date"] = frame["opened_date"].map(_to_iso_date)
    return frame[list(DIM_STORE_COLUMNS)]


def build_dim_product(products: pd.DataFrame) -> pd.DataFrame:
    """Build the product dimension with deterministic surrogate keys.

    ``list_unit_price`` is the **master-data** price and is stored as such. The
    price a line actually rang at lives on the fact row. PR-02 is the reason that
    distinction is load-bearing: P005's list price rose to 150.11 after every one
    of its 20 transactions had already rung at 141.61, so using the dimension's
    price in a revenue calculation would overstate historical revenue by $8.50 per
    unit sold and erase the finding at the same time.

    Args:
        products: Cleaned products frame (contract §7b column list).

    Returns:
        A frame with :data:`DIM_PRODUCT_COLUMNS`, ordered by ``product_id``.

    Raises:
        WarehouseLoadError: On missing columns or a duplicated ``product_id``.

    Defects handled: PR-02, PR-03, PR-04 provenance flags reach storage
        (``price_conflict``, ``category_is_imputed``, ``price_is_imputed``);
        PR-01's de-duplication is independently verified by the duplicate check.
    """
    _require_columns(products, REQUIRED_PRODUCT_COLUMNS, "clean_products() output")
    frame = products.copy()
    frame["product_id"] = frame["product_id"].astype(str)

    duplicated = frame.loc[frame["product_id"].duplicated(keep=False), "product_id"]
    if not duplicated.empty:
        raise WarehouseLoadError(
            f"dim_product would contain duplicate product_id(s) "
            f"{sorted(set(duplicated))}. PR-01 (exact duplicate) and PR-02 (price "
            f"change) must both be resolved to one row per product in "
            f"src/cleaning/products.py — and resolved DIFFERENTLY from each other."
        )

    frame = frame.sort_values("product_id", kind="mergesort", ignore_index=True)
    frame["product_key"] = np.arange(1, len(frame) + 1, dtype="int64")
    frame["category_is_imputed"] = _to_flag(
        frame["category_is_imputed"], "products.category_is_imputed"
    )
    frame["price_is_imputed"] = _to_flag(
        frame["price_is_imputed"], "products.price_is_imputed"
    )
    frame["price_conflict"] = _to_flag(frame["price_conflict"], "products.price_conflict")
    frame["list_unit_price"] = (
        pd.to_numeric(frame["list_unit_price"], errors="coerce").round(MONEY_DECIMALS)
    )
    return frame[list(DIM_PRODUCT_COLUMNS)]


def build_dim_customer(transactions: pd.DataFrame) -> pd.DataFrame:
    """Build the customer dimension from the distinct customers in the fact feed.

    WHY the fact feed and not a customer master file: there is no customer master
    in this dataset. The dimension is therefore *inferred*, which means it contains
    exactly the customers who transacted and nobody else — a fact worth stating,
    because "customer count" from this warehouse means "customers who bought in the
    window", not "customers on file".

    The ``GUEST`` member: 40 transactions (TX-06) arrive with no customer id and are
    mapped to one shared sentinel rather than 40 invented identities. The full
    argument, including what that choice costs analytically, is in the
    ``dim_customer`` comment block in :file:`schema.sql`. The short version: minting
    40 ids would assert something the source never said (that these were 40
    different people), inflate the customer count, deflate revenue-per-customer, and
    produce ids that are unstable across loads. One member is honest but makes
    ``COUNT(DISTINCT customer_key)`` an under-count and turns GUEST into an
    artificial top spender — which is why ``is_guest`` is a first-class column and
    why ``top_customers_lifetime`` must exclude it explicitly.

    Args:
        transactions: Cleaned transactions with ``customer_id`` and ``is_guest``.

    Returns:
        A frame with :data:`DIM_CUSTOMER_COLUMNS`, ordered by ``customer_id``.

    Raises:
        WarehouseLoadError: If ``customer_id`` holds NULLs (TX-06 must already have
            been mapped to the sentinel) or if ``is_guest`` disagrees with it.

    Defects handled: TX-06 (guest checkouts) reaches storage as a flagged member
        rather than as dropped rows or as NULL keys.
    """
    _require_columns(
        transactions, ("customer_id", "is_guest"), "clean_transactions() output"
    )
    frame = transactions[["customer_id", "is_guest"]].copy()

    if frame["customer_id"].isna().any():
        raise WarehouseLoadError(
            f"{int(frame['customer_id'].isna().sum())} transaction(s) still have a NULL "
            f"customer_id. TX-06's decision (contract §7b) is to keep the rows and set "
            f"customer_id = {GUEST_CUSTOMER_ID!r} with is_guest = True; NULL here would "
            f"become a NULL foreign key and the row would be lost at load time."
        )

    frame["customer_id"] = frame["customer_id"].astype(str)
    frame["is_guest"] = _to_flag(frame["is_guest"], "transactions.is_guest")

    # WHY drop_duplicates on BOTH columns then check: if one row says CUST0247 is a
    # guest and another says it is not, the flag and the id disagree and the
    # schema's `is_guest = (customer_id = 'GUEST')` CHECK would fire with no
    # explanation. Catching it here names the customer.
    distinct = frame.drop_duplicates().sort_values(
        ["customer_id", "is_guest"], kind="mergesort", ignore_index=True
    )
    conflicting = distinct.loc[distinct["customer_id"].duplicated(keep=False), "customer_id"]
    if not conflicting.empty:
        raise WarehouseLoadError(
            f"customer_id(s) {sorted(set(conflicting))} appear with BOTH is_guest=True "
            f"and is_guest=False. The guest sentinel and the flag must agree."
        )

    # WHY derive the flag from the sentinel rather than trusting the input: the
    # schema enforces `is_guest = (customer_id = 'GUEST')`. Deriving it means a
    # cleaner that forgot the flag on one guest row still produces a loadable,
    # correct dimension — and the disagreement check above already refused any
    # genuinely contradictory input.
    distinct["is_guest"] = (distinct["customer_id"] == GUEST_CUSTOMER_ID).astype("int64")
    distinct["customer_key"] = np.arange(1, len(distinct) + 1, dtype="int64")
    return distinct[list(DIM_CUSTOMER_COLUMNS)]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 · Fact construction and key resolution
# ══════════════════════════════════════════════════════════════════════════════
def _resolve_key(
    fact: pd.DataFrame,
    dimension: pd.DataFrame,
    natural_column: str,
    key_column: str,
    dimension_name: str,
    audit: AuditLog | None = None,
    defect_code: DefectCode | None = None,
) -> pd.DataFrame:
    """Join a natural key to its surrogate key and assert that nothing is orphaned.

    This is the step surrogate keys buy you, and the step that can go wrong. A
    left join is used deliberately: an inner join would *silently drop* every
    unresolvable row, which is the failure mode this project exists to argue
    against. The left join keeps the row, the NULL surrogate makes the failure
    visible, and the assertion turns it into a crash with the offending keys named.

    Args:
        fact: The in-progress fact frame.
        dimension: The dimension frame, carrying ``natural_column`` and
            ``key_column``.
        natural_column: Join column present in both frames.
        key_column: Surrogate key column to attach.
        dimension_name: Table name, for messages.
        audit: Optional ledger. Offending rows are quarantined into it *before*
            the raise, so the evidence survives the failure.
        defect_code: Which defect an orphan of this kind would be (TX-04/TX-05).

    Returns:
        ``fact`` with ``key_column`` attached, all non-null.

    Raises:
        UnresolvedKeyError: If any row failed to resolve.

    Defects handled: TX-04 (orphan store), TX-05 (orphan product) — as a
        last-line-of-defence detector, not as their owner.
    """
    merged = fact.merge(
        dimension[[natural_column, key_column]],
        on=natural_column,
        how="left",
        # WHY validate="many_to_one": this is the guard against join fan-out. If
        # the dimension ever contained two rows for one natural key (a regression
        # in ST-02 or PR-01/PR-02 handling), an unvalidated merge would silently
        # DUPLICATE fact rows and inflate revenue — a bug that shows up as
        # "revenue is up 6%" rather than as an error.
        validate="many_to_one",
    )
    unresolved = merged[key_column].isna()
    if unresolved.any():
        offending = sorted(merged.loc[unresolved, natural_column].astype(str).unique())
        if audit is not None and defect_code is not None:
            # WHY quarantine before raising: the run is about to die, and the rows
            # that killed it are the most useful thing a reviewer could be handed.
            audit.quarantine("transactions", merged.loc[unresolved].copy(), defect_code)
        raise UnresolvedKeyError(
            dimension=dimension_name,
            natural_column=natural_column,
            offending_keys=offending,
            row_count=int(unresolved.sum()),
        )
    merged[key_column] = merged[key_column].astype("int64")
    return merged


def build_fact_sales(
    transactions: pd.DataFrame,
    dim_date: pd.DataFrame,
    dim_store: pd.DataFrame,
    dim_product: pd.DataFrame,
    dim_customer: pd.DataFrame,
    audit: AuditLog | None = None,
) -> pd.DataFrame:
    """Assemble ``fact_sales`` at one-row-per-source-transaction grain.

    The three money columns are carried through, not recomputed:

    * ``net_amount`` **is** the cleaned ``total_amount``, byte for byte. Contract
      §7b: the reported total is authoritative. Recomputing it as
      ``quantity * unit_price`` is the previous solution's worst bug — it would
      overstate revenue by the entire value of the 20 seeded discounts (TX-03) and
      delete the finding in the process.
    * ``extended_amount`` is the list value of the line, present only so the
      discount is a visible number.
    * ``discount_amount`` is their difference, so ``revenue_reconciliation`` ties
      out arithmetically rather than by assertion.

    Args:
        transactions: Cleaned transactions (contract §4 column list).
        dim_date: Output of :func:`build_dim_date`.
        dim_store: Output of :func:`build_dim_store`.
        dim_product: Output of :func:`build_dim_product`.
        dim_customer: Output of :func:`build_dim_customer`.
        audit: Optional ledger for quarantining unresolved rows before raising.

    Returns:
        A frame with :data:`FACT_SALES_COLUMNS`, ordered by ``transaction_id``.

    Raises:
        WarehouseLoadError: On missing columns or duplicate ``transaction_id``.
        UnresolvedKeyError: On any natural key with no dimension member.

    Defects handled: TX-03 (discount preserved), TX-09 (duplicate grain check),
        TX-10 (returns preserved and flagged) reach storage intact; TX-04/TX-05
        are re-detected here as a safety net.
    """
    _require_columns(
        transactions, REQUIRED_TRANSACTION_COLUMNS, "clean_transactions() output"
    )
    fact = transactions.copy()
    fact["transaction_id"] = fact["transaction_id"].astype(str)

    # ── Grain check ───────────────────────────────────────────────────────────
    # WHY here as well as UNIQUE(transaction_id) in the DDL: TX-09 seeds 15 exact
    # duplicates. The constraint would abort the load; this names the ids so the
    # reviewer knows whether de-duplication regressed or a genuinely new duplicate
    # appeared in the source.
    dup_ids = fact.loc[fact["transaction_id"].duplicated(keep=False), "transaction_id"]
    if not dup_ids.empty:
        raise WarehouseLoadError(
            f"fact_sales grain violated: {len(dup_ids)} row(s) share "
            f"{len(set(dup_ids))} transaction_id(s), e.g. {sorted(set(dup_ids))[:10]}. "
            f"The declared grain is one row per source transaction record (TX-09)."
        )

    # ── Natural keys, normalised for joining ──────────────────────────────────
    fact["store_id"] = fact["store_id"].astype(str)
    fact["product_id"] = fact["product_id"].astype(str)
    fact["customer_id"] = fact["customer_id"].astype(str)
    parsed_dates = pd.to_datetime(fact["transaction_date"], errors="coerce")
    if parsed_dates.isna().any():
        raise WarehouseLoadError(
            f"{int(parsed_dates.isna().sum())} transaction(s) have an unparseable "
            f"transaction_date and cannot be assigned a date_key (TX-01)."
        )
    fact["full_date"] = parsed_dates.dt.strftime("%Y-%m-%d")

    # ── Resolve every natural key -> surrogate key ────────────────────────────
    # WHY dim_date is resolved by joining on the date string rather than by
    # computing yyyymmdd arithmetically: computing it would always "succeed", even
    # for a date outside the loaded calendar range, and the failure would surface
    # much later as a foreign key error with no context. Joining proves membership.
    fact = _resolve_key(fact, dim_date, "full_date", "date_key", "dim_date")
    fact = _resolve_key(
        fact, dim_store, "store_id", "store_key", "dim_store",
        audit=audit, defect_code=DefectCode.TX_04_ORPHAN_STORE,
    )
    fact = _resolve_key(
        fact, dim_product, "product_id", "product_key", "dim_product",
        audit=audit, defect_code=DefectCode.TX_05_ORPHAN_PRODUCT,
    )
    fact = _resolve_key(fact, dim_customer, "customer_id", "customer_key", "dim_customer")

    # ── Measures ──────────────────────────────────────────────────────────────
    fact["quantity"] = pd.to_numeric(fact["quantity"], errors="raise").astype("int64")
    fact["unit_price"] = pd.to_numeric(fact["unit_price"], errors="raise").round(MONEY_DECIMALS)
    fact["extended_amount"] = (
        pd.to_numeric(fact["extended_amount"], errors="raise").round(MONEY_DECIMALS)
    )
    fact["discount_amount"] = (
        pd.to_numeric(fact["discount_amount"], errors="raise").round(MONEY_DECIMALS)
    )
    # THE revenue column. Copied, never derived.
    fact["net_amount"] = (
        pd.to_numeric(fact["total_amount"], errors="raise").round(MONEY_DECIMALS)
    )
    fact["is_return"] = _to_flag(fact["is_return"], "transactions.is_return")

    # ── Deterministic sales_key ───────────────────────────────────────────────
    # WHY sort by transaction_id rather than letting AUTOINCREMENT allocate in
    # arrival order: identical input must produce an identical file (see the
    # SECTION 2 banner). transaction_id is unique by the grain check above, so the
    # sort is total and needs no tie-breaker.
    fact = fact.sort_values("transaction_id", kind="mergesort", ignore_index=True)
    fact["sales_key"] = np.arange(1, len(fact) + 1, dtype="int64")
    return fact[list(FACT_SALES_COLUMNS)]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 · SQL execution helpers
# ══════════════════════════════════════════════════════════════════════════════
def read_schema_statements(path: Path | str = SCHEMA_PATH) -> list[str]:
    """Read :file:`schema.sql` and split it into individually executable statements.

    WHY not ``sqlite3.Connection.executescript``: that method issues an implicit
    ``COMMIT`` before running the script. Using it would end the surrounding
    transaction, and the "whole load is atomic, roll back on failure" guarantee in
    contract §5 would quietly stop being true — a partial warehouse could then be
    left on disk by a failure during the fact load. Splitting the file and running
    each statement with ``execute()`` keeps everything inside one transaction.

    The splitter is a small character scanner rather than ``sql.split(";")``
    because the DDL contains ``--`` comments (which can contain semicolons in
    prose) and single-quoted literals such as ``'%Y-%m-%d'``.

    Args:
        path: The DDL file.

    Returns:
        Statements in file order, comments and blank lines removed.

    Raises:
        WarehouseLoadError: If the file is missing or yields no statements.

    Defects handled: none (DDL loading).
    """
    sql_path = Path(path)
    if not sql_path.is_file():
        raise WarehouseLoadError(f"Schema DDL not found at {sql_path}.")
    text = sql_path.read_text(encoding="utf-8")

    statements: list[str] = []
    buffer: list[str] = []
    in_string = False
    in_comment = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_comment:
            if char == "\n":
                in_comment = False
                buffer.append("\n")
            index += 1
            continue
        if in_string:
            buffer.append(char)
            if char == "'":
                in_string = False
            index += 1
            continue
        if char == "'":
            in_string = True
            buffer.append(char)
            index += 1
            continue
        if char == "-" and text.startswith("--", index):
            in_comment = True
            index += 2
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    if not statements:
        raise WarehouseLoadError(f"{sql_path} contained no SQL statements.")
    return statements


def _insert(conn: sqlite3.Connection, table: str, columns: Sequence[str],
            df: pd.DataFrame) -> int:
    """Insert a frame into ``table`` with an explicit column list.

    Args:
        conn: Open connection inside the load transaction.
        table: Target table.
        columns: Column order for the INSERT.
        df: Rows to insert.

    Returns:
        The number of rows inserted.

    Defects handled: none (write helper).
    """
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    rows = _records(df, columns)
    if rows:
        conn.executemany(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})", rows
        )
    return len(rows)


def probe_foreign_key_enforcement(conn: sqlite3.Connection) -> bool:
    """Prove ``PRAGMA foreign_keys = ON`` actually took effect, by breaking it.

    WHY this exists rather than trusting the pragma: SQLite silently ignores
    ``PRAGMA foreign_keys`` when it is issued inside a transaction, and it is a
    per-connection setting that does not persist in the file. A loader that sets it
    at the wrong moment gets no error, no warning, and a database in which the
    foreign keys are decorative. The only trustworthy check is to attempt a write
    that MUST fail.

    The probe inserts a fact row whose ``store_key`` cannot exist, inside a
    savepoint that is always rolled back, so it never touches the loaded data. If
    the insert succeeds, foreign keys are not being enforced and the caller must
    treat the load as unverified.

    Args:
        conn: A connection with the schema created and dimensions populated.

    Returns:
        True when the invalid insert was rejected (the healthy outcome).

    Defects handled: TX-04 / TX-05 — this is the proof that the database itself,
        not just the cleaning layer's opinion, refuses orphan references.
    """
    anchors = conn.execute(
        "SELECT (SELECT MIN(date_key) FROM dim_date), "
        "       (SELECT MIN(product_key) FROM dim_product), "
        "       (SELECT MIN(customer_key) FROM dim_customer)"
    ).fetchone()
    if any(value is None for value in anchors):
        # WHY not raise: an empty dimension is already reported by the row-count
        # verification; returning False here keeps the two failures separate.
        return False
    date_key, product_key, customer_key = anchors

    conn.execute("SAVEPOINT fk_probe")
    try:
        conn.execute(
            "INSERT INTO fact_sales (transaction_id, date_key, store_key, product_key, "
            "customer_key, quantity, unit_price, extended_amount, discount_amount, "
            "net_amount, is_return) VALUES (?, ?, ?, ?, ?, 1, 1.0, 1.0, 0.0, 1.0, 0)",
            # WHY -999999 for store_key and valid values everywhere else: the row
            # must violate EXACTLY ONE constraint. If it also broke a CHECK, a pass
            # would prove nothing about foreign keys.
            ("__FK_ENFORCEMENT_PROBE__", date_key, -999_999, product_key, customer_key),
        )
    except sqlite3.IntegrityError:
        # The expected, healthy path: SQLite refused the orphan.
        conn.execute("ROLLBACK TO fk_probe")
        conn.execute("RELEASE fk_probe")
        return True
    conn.execute("ROLLBACK TO fk_probe")
    conn.execute("RELEASE fk_probe")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 · Post-load verification
# ══════════════════════════════════════════════════════════════════════════════
def verify_warehouse(
    conn: sqlite3.Connection, transactions: pd.DataFrame, expected: dict[str, int]
) -> LoadVerification:
    """Read the warehouse back and prove it matches what was supposed to go in.

    Four independent checks, each catching a different class of mistake:

    1. **Row counts** — read back with ``COUNT(*)``, compared against what the
       loader believes it inserted. Catches a silently rejected batch.
    2. **``PRAGMA foreign_key_check``** — a full scan for dangling references.
       Complements the FK constraints, which only check rows as they are written.
    3. **FK enforcement probe** — see :func:`probe_foreign_key_enforcement`.
    4. **Revenue tie-out** — ``SUM(fact_sales.net_amount)`` must equal
       ``SUM(cleaned transactions.total_amount)`` to the cent. This is the check
       that would have caught the previous solution's headline bug: recomputing
       ``total_amount`` as ``quantity * unit_price`` inflates the warehouse total
       above the source total by the value of the 20 seeded discounts, and every
       downstream revenue number inherits the error.

    Args:
        conn: Connection inside the load transaction, after all inserts.
        transactions: The cleaned frame that was loaded — the source of truth for
            the tie-out.
        expected: ``{table: rows_the_loader_inserted}``.

    Returns:
        A populated :class:`LoadVerification`. ``.ok`` is the verdict.

    Defects handled: TX-03 (the tie-out is what makes "we preserved the discount"
        checkable), TX-04/TX-05 (integrity), TX-09/TX-10 (row-count completeness).
    """
    result = LoadVerification()

    # ── 1 · row counts, read back from the database ───────────────────────────
    for table in TABLE_LOAD_ORDER:
        count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        result.row_counts[table] = count
        if table in expected and count != expected[table]:
            result.problems.append(
                f"{table}: inserted {expected[table]} rows but COUNT(*) reports {count}."
            )

    result.source_rows = len(transactions)
    if result.row_counts.get("fact_sales", 0) != result.source_rows:
        result.problems.append(
            f"fact_sales holds {result.row_counts.get('fact_sales')} rows but the cleaned "
            f"feed had {result.source_rows}. The fact grain is one row per source "
            f"transaction record — no row may be added or lost at load time."
        )

    # ── 2 · referential integrity, checked exhaustively ───────────────────────
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    result.fk_violations = len(violations)
    if violations:
        result.problems.append(
            f"PRAGMA foreign_key_check returned {len(violations)} dangling reference(s): "
            f"{violations[:5]}"
        )

    # ── 3 · the pragma is actually in force ───────────────────────────────────
    result.fk_enforcement_proven = probe_foreign_key_enforcement(conn)
    if not result.fk_enforcement_proven:
        result.problems.append(
            "PRAGMA foreign_keys is NOT being enforced: a deliberately orphaned insert "
            "was accepted. Every referential guarantee in this schema is void."
        )

    # ── 4 · revenue ties out to the cent ──────────────────────────────────────
    warehouse_revenue = conn.execute(
        "SELECT COALESCE(SUM(net_amount), 0.0) FROM fact_sales"
    ).fetchone()[0]
    source_revenue = float(
        pd.to_numeric(transactions["total_amount"], errors="raise").sum()
    )
    result.warehouse_revenue = round(float(warehouse_revenue), MONEY_DECIMALS)
    result.source_revenue = round(source_revenue, MONEY_DECIMALS)
    result.revenue_delta = round(result.warehouse_revenue - result.source_revenue, 4)
    # WHY compare against half a cent rather than PRICE_TOLERANCE: the tolerance
    # exists to separate real discounts from float noise on a single row. This is a
    # sum over hundreds of rows and must be exact once rounded to cents, so anything
    # that would round to a non-zero number of cents is a genuine discrepancy.
    if abs(result.revenue_delta) >= 0.005:
        result.problems.append(
            f"Revenue tie-out FAILED: fact_sales SUM(net_amount) = "
            f"{result.warehouse_revenue:,.2f} but the cleaned transactions sum to "
            f"{result.source_revenue:,.2f} (delta {result.revenue_delta:+,.2f}). The "
            f"most likely cause is total_amount having been recomputed as "
            f"quantity * unit_price somewhere, which destroys TX-03 "
            f"(tolerance: {PRICE_TOLERANCE} per row)."
        )
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 · Filesystem handling (missing dir, locked or stale database)
# ══════════════════════════════════════════════════════════════════════════════
_SQLITE_SIDECAR_SUFFIXES: tuple[str, ...] = ("-journal", "-wal", "-shm")
"""Transient files SQLite writes beside a database. They are never project content:
a ``warehouse.db-journal`` surviving a run means a previous process died mid-write,
and leaving it next to a freshly replaced database invites SQLite to "roll back"
the new file using a journal that belongs to the old one."""


def _prepare_destination(db_path: Path) -> None:
    """Make sure the destination is writable before any work is done.

    Handles the three states the output directory is realistically in:

    * **Missing** — ``output/`` does not exist on a fresh clone (it is gitignored
      except for ``.gitkeep``). Created, with parents.
    * **Stale** — a ``-journal``/``-wal``/``-shm`` sidecar left by a crashed run.
      Removed, because SQLite will otherwise try to recover the *old* database
      using it after the new file is moved into place.
    * **Locked** — a DB browser, a notebook or a previous run still holds the file.
      On Windows this makes the final ``os.replace`` fail with ``PermissionError``.
      Detected here, up front, so the failure arrives before several seconds of
      work rather than after it.

    Args:
        db_path: Final destination for the database.

    Raises:
        WarehouseLoadError: If the existing file cannot be opened for writing.

    Defects handled: none (operational robustness).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:  # pragma: no cover - the lock check below reports it
                pass

    if db_path.exists():
        try:
            # WHY "r+b" and not "w": opening for write would TRUNCATE the existing
            # warehouse, destroying a perfectly good database before we know
            # whether the new one can even be built.
            with open(db_path, "r+b"):
                pass
        except OSError as exc:
            raise WarehouseLoadError(
                f"{db_path} exists but cannot be opened for writing ({exc}). It is "
                f"probably held open by another process — a SQLite browser, a notebook "
                f"with a live connection, or a previous run that has not exited. Close "
                f"it and re-run; the loader will not silently write somewhere else."
            ) from exc


def _atomic_replace(temp_db: Path, db_path: Path, attempts: int = 5) -> None:
    """Move the freshly built database over the destination, atomically.

    WHY build into a scratch file and move, rather than writing the destination in
    place:

    * **Nothing partial is ever visible.** ``os.replace`` is atomic on POSIX and on
      Windows, so a reader (the analytics stage, the dashboard) sees either the
      complete previous warehouse or the complete new one.
    * **A failed load leaves the previous warehouse intact.** Dropping and
      recreating tables in the destination would destroy the old data before
      knowing the new data is loadable.
    * **It makes the output byte-reproducible.** Rebuilding inside an existing
      database reuses freed pages, so the file layout depends on what was there
      before. Every run starts from an empty file and performs an identical
      sequence of operations, so identical input yields an identical file.

    Args:
        temp_db: The completed scratch database.
        db_path: Destination.
        attempts: Retries before giving up. WHY retry at all: on Windows an
            antivirus or search indexer can hold a brief shared lock on a file that
            was just closed, and failing the whole pipeline over a 200 ms window
            would be maddening.

    Raises:
        WarehouseLoadError: If the destination stays locked.

    Defects handled: none (operational robustness).
    """
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(temp_db, db_path)
            return
        except OSError as exc:  # PermissionError on Windows when the target is open
            last_error = exc
            time.sleep(0.2 * (attempt + 1))
    temp_db.unlink(missing_ok=True)
    raise WarehouseLoadError(
        f"Could not move the newly built warehouse into place at {db_path} after "
        f"{attempts} attempts ({last_error}). The destination is locked by another "
        f"process. The previous warehouse is unchanged and the new one was discarded."
    )


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 · Entry point
# ══════════════════════════════════════════════════════════════════════════════
def load_warehouse(
    stores: pd.DataFrame,
    products: pd.DataFrame,
    transactions: pd.DataFrame,
    db_path: Path | str = DB_PATH,
    audit: AuditLog | None = None,
    as_of_date: dt.date | None = AS_OF_DATE,
) -> dict[str, int]:
    """Build ``output/warehouse.db`` from the cleaned frames, atomically.

    Sequence, and why it is this sequence:

    1. **Build every frame in memory first.** Dimension construction and key
       resolution are pure functions that can fail; running them before a single
       byte is written means the common failure (a cleaning-layer escape) never
       even opens a database.
    2. **Open a scratch database beside the destination** and set
       ``PRAGMA foreign_keys = ON`` *outside* any transaction — inside one it is
       silently ignored.
    3. **BEGIN.** DDL, dimensions, fact, verification and COMMIT all happen inside
       this single transaction. SQLite makes DDL transactional, so even the
       ``DROP TABLE``/``CREATE TABLE`` pair rolls back.
    4. **Verify before committing.** Row counts, ``PRAGMA foreign_key_check``, an
       FK-enforcement probe and a revenue tie-out to the cent. A failure here rolls
       everything back, so a warehouse that does not tie out is never produced —
       as opposed to being produced and then reported as suspect in a log nobody
       reads.
    5. **VACUUM, then atomically move into place.** See :func:`_atomic_replace`.

    Idempotency: running this twice over the same input produces byte-identical
    files. Every dimension's surrogate keys come from a natural-key sort, the fact's
    ``sales_key`` comes from a ``transaction_id`` sort, each run starts from an
    empty scratch file and performs the same operations in the same order, and the
    final VACUUM normalises the page layout. Nothing time-dependent is stored.

    Args:
        stores: Cleaned stores (contract §7b columns).
        products: Cleaned products (contract §7b columns).
        transactions: Cleaned transactions (contract §4 columns).
        db_path: Destination file. Parent directories are created.
        audit: Optional :class:`~src.audit.AuditLog`. Used to quarantine offending
            rows before raising on an unresolved key, so the evidence survives the
            crash. No :class:`~src.audit.DefectRecord` is written: the loader
            detects no defects, and the audit's ``action`` vocabulary and
            expected-count proof describe *source data* defects, not load
            assertions. Verification results are returned instead.
        as_of_date: Reference "today" (contract §2), used to extend ``dim_date``
            through the end of the trailing analytics window.

    Returns:
        ``{"dim_date": n, "dim_store": n, "dim_product": n, "dim_customer": n,
        "fact_sales": n, "fk_violations": 0, "revenue_tie_out_cents": 0}``. The two
        trailing entries are verification results, returned rather than merely
        logged so the pipeline's run metadata and the dashboard both carry the
        proof rather than a claim.

    Raises:
        WarehouseLoadError: On a contract breach in the input frames, a
            verification failure, or an unwritable destination.
        UnresolvedKeyError: When a fact row references a natural key with no
            dimension member (a subclass of the above).

    Defects handled: none owned. TX-03 is carried into storage unmodified, TX-06
        and TX-10 reach the warehouse as flagged rows rather than as deletions, and
        TX-04/TX-05 are independently re-detected by key resolution and by the
        database's own foreign keys.
    """
    target = Path(db_path)

    # ── Step 1 · build everything in memory (pure, no side effects) ───────────
    dim_date = build_dim_date(transactions, as_of_date=as_of_date)
    dim_store = build_dim_store(stores)
    dim_product = build_dim_product(products)
    dim_customer = build_dim_customer(transactions)
    fact_sales = build_fact_sales(
        transactions, dim_date, dim_store, dim_product, dim_customer, audit=audit
    )
    frames: dict[str, tuple[tuple[str, ...], pd.DataFrame]] = {
        "dim_date": (DIM_DATE_COLUMNS, dim_date),
        "dim_store": (DIM_STORE_COLUMNS, dim_store),
        "dim_product": (DIM_PRODUCT_COLUMNS, dim_product),
        "dim_customer": (DIM_CUSTOMER_COLUMNS, dim_customer),
        "fact_sales": (FACT_SALES_COLUMNS, fact_sales),
    }

    statements = read_schema_statements()

    # ── Step 2 · scratch database beside the destination ──────────────────────
    _prepare_destination(target)
    handle, temp_name = tempfile.mkstemp(
        # WHY the same directory: os.replace is only atomic within one filesystem.
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".building"
    )
    os.close(handle)
    temp_db = Path(temp_name)
    temp_db.unlink()  # WHY: sqlite3 wants to create the file itself, not adopt one.

    verification: LoadVerification
    # WHY isolation_level=None: it disables the sqlite3 module's implicit
    # transaction management, which would otherwise open and commit transactions
    # around statements on its own schedule. Explicit BEGIN/COMMIT/ROLLBACK is the
    # only way to guarantee the whole load is one unit.
    conn = sqlite3.connect(str(temp_db), isolation_level=None)
    try:
        # WHY before BEGIN: SQLite silently ignores this pragma inside a
        # transaction. Issued here it takes effect; the read-back asserts it did,
        # and probe_foreign_key_enforcement() later proves it behaviourally.
        conn.execute("PRAGMA foreign_keys = ON")
        if int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise WarehouseLoadError(
                "PRAGMA foreign_keys = ON did not take effect; refusing to build a "
                "warehouse whose referential constraints are unenforced."
            )

        conn.execute("BEGIN")
        try:
            # -- DDL (drop + create), inside the transaction ------------------
            for statement in statements:
                conn.execute(statement)

            # -- dimensions, then the fact ------------------------------------
            inserted: dict[str, int] = {}
            for table in TABLE_LOAD_ORDER:
                columns, frame = frames[table]
                inserted[table] = _insert(conn, table, columns, frame)

            # -- verify BEFORE committing -------------------------------------
            verification = verify_warehouse(conn, transactions, inserted)
            if not verification.ok:
                raise WarehouseLoadError(
                    "Warehouse verification failed; rolling back so no partial or "
                    "untrustworthy database is produced:\n  - "
                    + "\n  - ".join(verification.problems)
                )
            conn.execute("COMMIT")
        except BaseException:
            # WHY BaseException: a KeyboardInterrupt mid-load must also roll back,
            # otherwise Ctrl-C leaves a half-populated scratch database that the
            # next step would happily move into place.
            conn.execute("ROLLBACK")
            raise

        # -- VACUUM outside the transaction, for a canonical page layout ------
        # WHY: it defragments and rewrites the file deterministically, which is
        # what makes two runs byte-comparable rather than merely equivalent.
        conn.execute("VACUUM")
    except BaseException:
        conn.close()
        temp_db.unlink(missing_ok=True)
        raise
    conn.close()

    # ── Step 3 · publish atomically ───────────────────────────────────────────
    _atomic_replace(temp_db, target)

    print(
        f"  verify  foreign_key_check: {verification.fk_violations} violation(s); "
        f"enforcement proven by rejected probe insert: "
        f"{verification.fk_enforcement_proven}"
    )
    print(
        f"  verify  revenue tie-out: fact_sales SUM(net_amount)="
        f"{verification.warehouse_revenue:,.2f} vs cleaned total_amount="
        f"{verification.source_revenue:,.2f} "
        f"(delta {verification.revenue_delta_cents} cent(s))"
    )

    counts: dict[str, int] = {table: verification.row_counts[table] for table in TABLE_LOAD_ORDER}
    counts["fk_violations"] = verification.fk_violations
    counts["revenue_tie_out_cents"] = verification.revenue_delta_cents
    return counts


# WHY these aliases: src/pipeline.py resolves the warehouse entry point by trying
# 'load_warehouse', then 'build_warehouse', then 'load' (contract §7b). The
# canonical name is the first; the aliases exist so a rename anywhere in the chain
# cannot break the orchestrator.
build_warehouse = load_warehouse
load = load_warehouse


__all__ = [
    "DIM_CUSTOMER_COLUMNS",
    "DIM_DATE_COLUMNS",
    "DIM_PRODUCT_COLUMNS",
    "DIM_STORE_COLUMNS",
    "FACT_SALES_COLUMNS",
    "LoadVerification",
    "SCHEMA_PATH",
    "TABLE_LOAD_ORDER",
    "UnresolvedKeyError",
    "WarehouseLoadError",
    "build_dim_customer",
    "build_dim_date",
    "build_dim_product",
    "build_dim_store",
    "build_fact_sales",
    "build_warehouse",
    "load",
    "load_warehouse",
    "probe_foreign_key_enforcement",
    "read_schema_statements",
    "verify_warehouse",
]
