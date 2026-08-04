"""Store dimension cleaning -- owns ST-01, ST-02 and ST-03.

What this module does
---------------------
Takes the 16 raw rows of ``data/raw/stores.csv`` exactly as they sit on disk
(all-string, courtesy of :func:`src.io_utils.read_csv_as_str`) and returns the
15-row, fully typed store dimension that ``src/warehouse/loader.py`` loads into
``dim_store``. Every transformation on the way is detected first, decided
deliberately, and written into the :class:`~src.audit.AuditLog` so a reviewer can
see what changed and argue with why.

Defect codes owned
------------------
=======  ==========================================  ================================
Code     Problem                                     Handler
=======  ==========================================  ================================
ST-01    S003 zip ``'0938'`` -- 4 chars, leading      :func:`normalize_zip_codes`
         zero eaten upstream (1 row)
ST-02    S007 appears twice with a contested          :func:`resolve_store_survivorship`
         ``store_name`` (1 surplus row)
ST-03    S013 / S014 have a NULL ``region``           :func:`impute_region`
         (2 rows)
=======  ==========================================  ================================

Inputs
------
``pd.DataFrame`` with the seven raw string columns
(``store_id, store_name, city, state, zip_code, region, opened_date``) plus an
:class:`~src.audit.AuditLog` that is **mutated in place** (contract §4 -- the
audit is never returned separately).

Outputs
-------
``pd.DataFrame`` with :data:`OUTPUT_COLUMNS`, one row per surviving
``store_id``:

``store_id, store_name, city, state`` (str), ``zip_code`` (str, 5 chars),
``zip_is_suspect`` (bool), ``region`` (str, never null), ``region_is_imputed``
(bool), ``opened_date`` (``datetime64[ns]``).

Stage ordering is load-bearing and is justified inline in :func:`clean_stores`.
"""

from __future__ import annotations

from typing import Final, Sequence

import pandas as pd

from src.audit import AuditLog, DefectRecord
# F16: the quarantine disposition vocabulary is shared with products.py and
# defined once in config.py, so the two modules cannot drift to different
# spellings of "this row was dropped" versus "this row survived".
from src.config import DISPOSITION_DROPPED, DISPOSITION_EVIDENCE, ZIP_CODE_LENGTH
from src.defects import DefectCode

# ── Column contracts ──────────────────────────────────────────────────────────
# WHY these are named constants rather than inline literals: three other modules
# (profiler, warehouse loader, tests) assert against this exact shape. A rename
# should break in one place, loudly, not in four places, subtly.
SOURCE_COLUMNS: Final[tuple[str, ...]] = (
    "store_id",
    "store_name",
    "city",
    "state",
    "zip_code",
    "region",
    "opened_date",
)

OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    "store_id",
    "store_name",
    "city",
    "state",
    "zip_code",
    "zip_is_suspect",
    "region",
    "region_is_imputed",
    "opened_date",
)

BUSINESS_KEY: Final[str] = "store_id"
"""Natural key. ``dim_store.store_id`` carries a UNIQUE constraint, so an
unresolved duplicate here is not a bad number -- it is a failed load."""

OPENED_DATE_FORMAT: Final[str] = "%Y-%m-%d"
"""The one format this column is actually written in.

WHY an explicit format string instead of letting pandas infer: inference is what
turned TX-01 into a silent data-loss bug in the previous attempt. The store file
happens to be uniformly ISO today, but stating the format means a future file
that is *not* ISO fails a visible assertion instead of being quietly reinterpreted
day-first.
"""


# ══════════════════════════════════════════════════════════════════════════════
# Shared normalisation
# ══════════════════════════════════════════════════════════════════════════════
def normalize_text_columns(df: pd.DataFrame, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Strip surrounding whitespace and turn empty strings into ``NaN``.

    Args:
        df: All-string frame straight from :func:`src.io_utils.read_csv_as_str`.
        columns: Columns to normalise. Defaults to every column present.

    Returns:
        A copy with the requested columns trimmed and blanks unified to ``NaN``.

    Defects handled: none on its own, but it is a precondition for ST-01
        (``' 0938 '`` must fail the five-digit test on its digits, not on its
        padding) and ST-03 (``''`` and ``NaN`` must be the same kind of missing,
        otherwise ``.isna()`` under-counts the NULL regions).
    """
    out = df.copy()
    targets = list(columns) if columns is not None else list(out.columns)
    for col in targets:
        if col not in out.columns:
            continue
        # WHY the object-dtype guard: this function is deliberately safe to call
        # on a partially-typed frame, so a datetime or float column passed in by
        # a future caller is left alone rather than stringified.
        if out[col].dtype != object:
            continue
        stripped = out[col].astype("string").str.strip()
        # WHY replace("" -> NA) rather than leaving it: a blank cell and a NULL
        # cell mean the same thing here, and letting both exist would mean every
        # downstream null check has to remember to test for two things.
        out[col] = stripped.replace({"": pd.NA}).astype(object).where(lambda s: s.notna(), other=None)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# ST-01 · Malformed ZIP code
# ══════════════════════════════════════════════════════════════════════════════
PADDABLE_ZIP_PATTERN: Final[str] = r"[0-9]{1,%d}" % ZIP_CODE_LENGTH
"""The ONLY shape of value ST-01 is allowed to left-pad: non-empty, all digits,
no longer than five characters.

M12: declared as a named module constant, not inlined into the mask, because the
guard is the whole decision. Left-padding ``'0938'`` recovers a leading zero an
upstream spreadsheet ate; left-padding ``'N/A'`` or ``'1234-5678'`` fabricates a
ZIP that existed in no encoding of the source and then labels it merely
"unverified". An adversarial audit replaced the guarded assignment with a blanket
``zfill(5)`` -- the previous solution's named bug #5 -- and the whole suite passed,
because every other ZIP in this particular file is already five characters. The
constant, the post-condition assertion in :func:`normalize_zip_codes` and the
``padding_applied`` column in the quarantine CSV exist so that cannot recur
quietly."""


def normalize_zip_codes(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Left-pad malformed ZIPs to five characters and flag them as unverifiable.

    ── ST-01 · Malformed ZIP code ──────────────────────────────────────────────
    WHY: S003 (Greece Ridge Center, NY) carries ``'0938'`` -- four characters.
      The story is an upstream spreadsheet that read the field as a number and
      ate the leading zero. Reading the CSV with ``dtype=str`` is the only reason
      the defect is still visible at all: infer types and ``'0938'`` becomes the
      integer ``938``, at which point it is indistinguishable from a genuinely
      short number and the leading-zero explanation is unrecoverable.
    DECISION: pad *only the rows that fail* ``^[0-9]{5}$`` to five characters,
      producing ``'00938'``, and raise ``zip_is_suspect`` on exactly those rows.
      The padded value is presented as *plausibly re-encoded*, never as corrected:
      ``00938`` is structurally valid but it is not a real New York ZIP -- the
      006xx-009xx range belongs to Puerto Rico and Greece, NY is 14xxx. We do not
      know which digit was eaten and this file cannot tell us, so the field is
      made joinable and simultaneously marked for human verification.
    ALTERNATIVE REJECTED: ``zip_code.astype(str).str.zfill(5)`` across the whole
      column -- the previous solution's bug. It hides *which* row was ever wrong
      (every value comes out five characters, so no audit is possible after the
      fact) and it would silently mangle any legitimate ZIP+4 value into garbage.
    ALTERNATIVE ALSO REJECTED: dropping or NULLing S003 -- the store has real
      transactions, and losing a store to a cosmetic field defect trades a
      formatting problem for a revenue problem.

    Args:
        df: Whitespace-normalised source frame (all 16 rows, pre-de-duplication).
        audit: Ledger, mutated in place.

    Returns:
        A copy with ``zip_code`` padded where needed and a new boolean
        ``zip_is_suspect`` column.

    Defects handled: ST-01.
    """
    out = df.copy()
    zips = out["zip_code"].astype("string")

    # WHY count on the *source* frame, before de-duplication: DefectRecord
    # counts source rows observed, never post-filter survivors (contract §7b).
    well_formed = zips.str.fullmatch(rf"[0-9]{{{ZIP_CODE_LENGTH}}}").fillna(False)
    # WHY ``.notna() & ~well_formed`` rather than ``~well_formed``: a genuinely
    # missing ZIP is a different defect class with a different remedy, and
    # folding it in here would over-count ST-01 and hide the missing value.
    suspect_mask = zips.notna() & ~well_formed

    # ── M12 · the digit guard, stated as a rule and then enforced ────────────
    # WHY this is called out rather than left as one clever line: an adversarial
    #   audit replaced the guarded assignment with an unconditional
    #   ``zip_code.astype(str).str.zfill(5)`` -- verbatim the previous solution's
    #   named bug #5 -- and nothing objected. All 27 tests passed and the pipeline
    #   reported 17/17, because every other ZIP in *this* file already happens to
    #   be five characters, so the mutation was behaviourally inert on this data
    #   and would only surface the day a 'N/A' or a ZIP+4 arrived. A guard that
    #   nothing enforces is a comment.
    # THE RULE, in one sentence: a value is padded if and only if it is
    #   non-empty, entirely digits, and shorter than five characters.
    #   '0938' qualifies; 'N/A' does not; '12345-6789' does not; a well-formed
    #   '14626' is not a suspect in the first place and is never touched.
    # WHY it matters: zero-filling a non-numeric ZIP does not recover a lost digit,
    #   it invents a value that existed in no encoding of the source, and it does so
    #   under a flag ('zip_is_suspect') that says only "unverified" -- so the
    #   fabrication would travel downstream wearing the label of a real ZIP.
    paddable = suspect_mask & zips.str.fullmatch(PADDABLE_ZIP_PATTERN).fillna(False)
    out.loc[paddable, "zip_code"] = (
        zips[paddable].str.zfill(ZIP_CODE_LENGTH).astype(object)  # DEFECT: ST-01
    )

    # ── M12 · post-condition: prove the guard actually held ──────────────────
    # WHY assert on the result rather than trust the mask: this is the check that
    #   an unconditional ``zfill`` cannot pass. It compares the output column
    #   against the input column and demands that every difference be one the rule
    #   above sanctions. Anyone reintroducing the blanket zfill gets an
    #   AssertionError naming the row it mangled, on the very first run.
    changed = out["zip_code"].astype("string").ne(zips) & zips.notna()
    unsanctioned = changed & ~paddable
    assert not bool(unsanctioned.any()), (
        "ST-01 padded a ZIP the digit guard excludes: "
        f"{out.loc[unsanctioned, [BUSINESS_KEY]].to_dict('records')} "
        f"(source values {zips[unsanctioned].tolist()}). Padding is permitted ONLY for "
        f"non-empty all-digit values shorter than {ZIP_CODE_LENGTH} characters -- an "
        "unconditional zip_code.str.zfill(5) is the previous solution's bug #5 and would "
        "fabricate a ZIP out of a value like 'N/A' while flagging it merely 'unverified'."
    )

    # WHY a persisted flag rather than a comment in the README: the padded value
    # is now indistinguishable from a real ZIP by inspection, so the *only* way a
    # downstream analyst learns not to trust it is if the row carries the doubt
    # with it into the warehouse.
    out["zip_is_suspect"] = suspect_mask.fillna(False).to_numpy(dtype=bool)

    affected = out.loc[suspect_mask, BUSINESS_KEY].astype(str).tolist()
    detected = int(suspect_mask.sum())
    if detected:
        # WHY quarantine a row we are keeping: quarantine here means "a human
        # should look at this", not "this was thrown away". The CSV gives a data
        # steward the before/after side by side without reopening the raw file.
        evidence = df.loc[suspect_mask, list(SOURCE_COLUMNS)].copy()
        evidence["zip_code_padded"] = out.loc[suspect_mask, "zip_code"].to_numpy()
        evidence["note"] = "padded to 5 chars; NOT verified as a real ZIP"
        # F16: S003 is kept -- 16 raw stores become 15 because of ST-02, not this.
        # Labelling the row 'evidence' is what stops a reader subtracting it from
        # the store count and finding 14.
        evidence.insert(1, "disposition", DISPOSITION_EVIDENCE)
        # M12: record what the guard actually did on this run, so the padding
        # policy is visible in the artifact and not only in the code.
        evidence["padding_applied"] = paddable[suspect_mask].to_numpy()
        audit.quarantine("stores", evidence, DefectCode.ST_01_MALFORMED_ZIP)
        audit.record(
            DefectRecord(
                code=DefectCode.ST_01_MALFORMED_ZIP,
                detected_count=detected,
                # WHY "flagged" and not "imputed": the padding restores an
                # encoding, not a fact. Calling it imputation would overstate our
                # confidence in a value nobody has verified.
                action="flagged",
                affected_keys=affected,
                notes=(
                    "S003 zip '0938' left-padded to '00938' so the field is structurally "
                    "valid and joinable. zip_is_suspect=True because 00938 is a Puerto Rico "
                    "range value while the store is in Greece, NY (14xxx) -- padding restores "
                    "a well-formed field, not a true one. Requires source-system verification."
                ),
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════
# ST-02 · Near-duplicate primary key (survivorship)
# ══════════════════════════════════════════════════════════════════════════════
# The ranked rule, declared as data so it can be reviewed, reordered or extended
# without touching the algorithm below. Each entry is
# ``(criterion_name, series_builder, ascending)`` and they are applied in order.
SURVIVORSHIP_CRITERIA: Final[tuple[tuple[str, str, bool], ...]] = (
    ("fewest_nulls", "_null_count", True),
    ("earliest_opened_date", "_opened_sort", True),
    ("lexicographic_store_name", "_name_sort", True),
)
"""Golden-record election rule for a contested ``store_id``, in priority order.

1. **fewest nulls** across the source columns -- the most complete record wins,
   because completeness is the one quality signal available without knowing the
   business.
2. **earliest ``opened_date``** -- ties break toward the record that has been in
   the master data longest, i.e. the one other systems are most likely keyed to.
3. **lexicographic ``store_name``** -- a final, arbitrary-but-deterministic
   tiebreak. It is arbitrary *on purpose*: the point is that the outcome is
   reproducible and stated, not that alphabetical order is meaningful.

On this file, rules 1 and 2 both tie (the two S007 rows are equally complete and
share ``2006-01-22``), so rule 3 decides and elects **"Downtown Rochester"**.
"""


def _null_count_over(df: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """Count nulls per row across ``columns`` only.

    WHY restricted to the source columns: by the time survivorship runs, the
    frame also carries derived flags (``zip_is_suspect``) that are never null.
    Counting them would dilute the completeness signal with columns this
    pipeline invented, which is not evidence about the source record.
    """
    present = [c for c in columns if c in df.columns]
    return df[present].isna().sum(axis=1)


def _deciding_criterion(group: pd.DataFrame) -> str:
    """Name the first criterion on which a contested group actually differs.

    Args:
        group: All rows sharing one business key, already carrying the sort
            helper columns.

    Returns:
        The criterion name that broke the tie, or ``"row_order_fallback"`` if
        every declared criterion tied -- which would mean the rule is
        under-specified for this data and a reviewer should know.

    Defects handled: ST-02 (explanation only).
    """
    for name, column, _asc in SURVIVORSHIP_CRITERIA:
        if group[column].nunique(dropna=False) > 1:
            return name
    return "row_order_fallback"


def resolve_store_survivorship(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Collapse duplicate ``store_id``s using an explicit ranked survivorship rule.

    ── ST-02 · Near-duplicate primary key ──────────────────────────────────────
    WHY: S007 appears twice -- "Downtown Rochester" and "Rochester Downtown".
      City, state, ZIP and ``opened_date`` agree exactly, so ``store_name`` is
      the only contested attribute: these are two spellings of one real store,
      not two stores. Collapsing them is right; *how* they collapse is the whole
      question, because ``dim_store.store_id`` is UNIQUE and an unresolved
      collision is a failed load, not a bad number.
    DECISION: apply :data:`SURVIVORSHIP_CRITERIA` -- fewest nulls, then earliest
      ``opened_date``, then lexicographically first ``store_name`` -- and keep
      the top-ranked row per key. The losing record and the criterion that
      decided it are both written to the ledger and to quarantine, so the
      discarded variant is recoverable rather than merely gone.
    ALTERNATIVE REJECTED: ``drop_duplicates(subset=['store_id'], keep='first')``.
      It produces a *different winner* the moment the extract is re-sorted, which
      means the warehouse silently changes without a single line of code
      changing -- the definition of an unreproducible pipeline. It also records
      no reason, so nobody can ever argue with the choice.
    ALTERNATIVE REJECTED: keeping both rows and letting the UNIQUE constraint
      fail the load. Honest, but it stops the pipeline over a defect we can
      resolve deterministically; the constraint stays as a backstop instead.
    NOTE ON GENERALITY: nothing below is specific to S007 or to a two-row
      collision. The rank-then-take-first-per-group construction handles any
      number of contested keys with any number of rows each, and adding a fourth
      tiebreak is a one-line edit to :data:`SURVIVORSHIP_CRITERIA`.

    Args:
        df: Frame that may contain duplicate ``store_id`` values.
        audit: Ledger, mutated in place.

    Returns:
        A copy with exactly one row per ``store_id``, original column order and
        source row order preserved.

    Defects handled: ST-02.
    """
    out = df.copy()
    contested_mask = out[BUSINESS_KEY].duplicated(keep=False)
    if not bool(contested_mask.any()):
        # WHY record nothing rather than record zero: an absent record and a
        # zero-count record mean different things to
        # ``assert_all_expected_defects_found`` -- absent says "never checked",
        # which for a defect the catalog expects is exactly the alarm we want.
        return out

    # ── Build the ranking keys ───────────────────────────────────────────────
    ranked = out.copy()
    ranked["_null_count"] = _null_count_over(ranked, SOURCE_COLUMNS)
    # WHY errors="coerce" here specifically: an unparseable date must not crash
    # the survivorship pass. It sorts last (NaT) under ``na_position="last"``,
    # which is the correct behaviour anyway -- a record with an unreadable
    # opened_date should not win a tiebreak that is *about* opened_date.
    ranked["_opened_sort"] = pd.to_datetime(
        ranked["opened_date"], format=OPENED_DATE_FORMAT, errors="coerce"
    )
    # WHY fillna(""): a null name must still sort deterministically rather than
    # landing wherever the sort implementation happens to put NaN.
    ranked["_name_sort"] = ranked["store_name"].astype("string").fillna("")
    ranked["_source_order"] = range(len(ranked))

    sort_columns = [column for _name, column, _asc in SURVIVORSHIP_CRITERIA]
    ascending = [asc for _name, _column, asc in SURVIVORSHIP_CRITERIA]
    ranked = ranked.sort_values(
        by=[*sort_columns, "_source_order"],
        ascending=[*ascending, True],
        # WHY mergesort: it is the only stable sort pandas offers here, so the
        # explicit "_source_order" backstop actually holds and two runs over the
        # same data cannot disagree.
        kind="mergesort",
        na_position="last",
    )

    # ── Elect one winner per key ─────────────────────────────────────────────
    # WHY groupby(...).head(1) over the sorted frame rather than idxmin on a
    # composite score: a composite score would have to invent weights across
    # three incommensurable criteria (a null count, a date, a string). Sorting
    # applies them strictly in priority order, which is what the rule says.
    winners = ranked.groupby(BUSINESS_KEY, sort=False).head(1)  # DEFECT: ST-02
    loser_index = ranked.index.difference(winners.index)
    losers = ranked.loc[loser_index]

    # ── Explain each election, per key, for the ledger ────────────────────────
    explanations: list[str] = []
    evidence_rows: list[pd.DataFrame] = []
    for key, group in ranked[ranked[BUSINESS_KEY].isin(losers[BUSINESS_KEY])].groupby(
        BUSINESS_KEY, sort=True
    ):
        elected = group.iloc[0]
        rejected = group.iloc[1:]
        criterion = _deciding_criterion(group)
        explanations.append(
            f"{key}: elected '{elected['store_name']}' over "
            + ", ".join(f"'{n}'" for n in rejected["store_name"].tolist())
            + f" by {criterion}"
        )
        evidence = group[list(SOURCE_COLUMNS)].copy()
        outcomes = ["ELECTED", *["REJECTED"] * len(rejected)]
        evidence["survivorship_outcome"] = outcomes
        evidence["deciding_criterion"] = criterion
        evidence["null_count"] = group["_null_count"].to_numpy()
        # F16: ELECTED/REJECTED describes the contest; ``disposition`` describes
        # what happened to the row in the output, in the same vocabulary the
        # products quarantine and the transaction lineage use. A reader can now
        # count 'dropped' across every quarantine CSV in the directory and get the
        # exact difference between the raw row counts and the cleaned ones,
        # without knowing what each defect code means.
        evidence.insert(
            1,
            "disposition",
            [DISPOSITION_EVIDENCE if o == "ELECTED" else DISPOSITION_DROPPED for o in outcomes],
        )
        evidence_rows.append(evidence)

    if evidence_rows:
        audit.quarantine(
            "stores", pd.concat(evidence_rows, ignore_index=True), DefectCode.ST_02_NEAR_DUPLICATE_PK
        )

    audit.record(
        DefectRecord(
            code=DefectCode.ST_02_NEAR_DUPLICATE_PK,
            # WHY surplus rows rather than contested rows: the defect is the
            # *extra* record, and this framing matches PR-01/PR-02 in
            # products.py so the two datasets' numbers mean the same thing.
            detected_count=int(len(losers)),
            action="dropped",
            affected_keys=losers[BUSINESS_KEY].astype(str).tolist(),
            notes=(
                "Ranked survivorship rule (fewest nulls -> earliest opened_date -> "
                "lexicographic store_name), NOT keep='first'. "
                + "; ".join(explanations)
                + ". Both S007 rows agree on city/state/zip/opened_date and are equally "
                "complete, so the name tiebreak decides; the rejected variant is preserved "
                "in output/quarantine/stores__ST-02.csv."
            ),
        )
    )

    # WHY re-sort to source order before returning: the ranking order is an
    # implementation detail of this function, and letting it leak would make
    # every downstream artifact (cleaned CSV, dashboard table) reorder itself
    # whenever a tiebreak rule changed.
    survivors = winners.sort_values("_source_order", kind="mergesort")
    return survivors.drop(columns=["_null_count", "_opened_sort", "_name_sort", "_source_order"])


# ══════════════════════════════════════════════════════════════════════════════
# ST-03 · NULL region
# ══════════════════════════════════════════════════════════════════════════════
# WHY this map exists at all, given the rule is "use the observed vocabulary":
# the observed vocabulary tells us which *labels* are legal; it cannot tell us
# which label Oregon belongs to, because no Oregon row in this file has a region.
# So the map supplies the geography (US Census divisions) and the observed
# vocabulary supplies the naming -- and :func:`build_state_region_map` refuses to
# emit any label the column has not already used. That guard is precisely what
# the previous solution lacked when it invented "East".
CENSUS_REGION_BY_STATE: Final[dict[str, str]] = {
    # Northeast
    "CT": "Northeast", "ME": "Northeast", "MA": "Northeast", "NH": "Northeast",
    "NJ": "Northeast", "NY": "Northeast", "PA": "Northeast", "RI": "Northeast",
    "VT": "Northeast",
    # Midwest
    "IA": "Midwest", "IL": "Midwest", "IN": "Midwest", "KS": "Midwest",
    "MI": "Midwest", "MN": "Midwest", "MO": "Midwest", "ND": "Midwest",
    "NE": "Midwest", "OH": "Midwest", "SD": "Midwest", "WI": "Midwest",
    # South
    "AL": "South", "AR": "South", "DC": "South", "DE": "South", "FL": "South",
    "GA": "South", "KY": "South", "LA": "South", "MD": "South", "MS": "South",
    "NC": "South", "OK": "South", "SC": "South", "TN": "South", "TX": "South",
    "VA": "South", "WV": "South",
    # West
    "AK": "West", "AZ": "West", "CA": "West", "CO": "West", "HI": "West",
    "ID": "West", "MT": "West", "NM": "West", "NV": "West", "OR": "West",
    "UT": "West", "WA": "West", "WY": "West",
}

UNKNOWN_REGION: Final[str] = "Unknown"
"""Last-resort label, used only when neither the data nor the geography map can
place a state under a name the column already uses. It is never reached by this
dataset; it exists so that a future file with a state we cannot classify degrades
to a visible bucket instead of a NULL that vanishes from every GROUP BY."""


def build_state_region_map(df: pd.DataFrame) -> dict[str, str]:
    """Derive ``state -> region`` from the data first, geography second.

    Precedence, highest to lowest:

    1. **Observed pairing.** If any non-null row in this file already maps the
       state to a region, use that. This is direct evidence and needs no theory.
    2. **Census geography, vocabulary-checked.** Otherwise consult
       :data:`CENSUS_REGION_BY_STATE`, *but only accept the answer if that label
       already appears in this column*. OR is not in the file with a region, so
       it lands here: census says "West", and "West" is already how the file
       labels AZ and WA, so the imputed value adds no new vocabulary.
    3. **:data:`UNKNOWN_REGION`.** Neither source could answer.

    WHY the vocabulary check is the important line: the previous solution
    hard-coded ``NY -> "East"`` while the data says ``"Northeast"``. That
    invented a sixth region, split the Northeast in two, and quietly corrupted
    average-order-value by region -- a wrong answer that looked entirely
    reasonable on a chart. Refusing to emit a label the column has never used
    makes that whole class of bug unreachable.

    Args:
        df: Frame containing ``state`` and ``region`` columns.

    Returns:
        ``{"NY": "Northeast", ..., "OR": "West"}`` covering every state present.

    Raises:
        ValueError: If a state is observed with two different regions -- an
            ambiguity we must not resolve by silently picking one.

    Defects handled: ST-03 (supporting rule).
    """
    known = df[df["region"].notna() & df["state"].notna()]
    vocabulary = set(known["region"].astype(str).unique())

    observed: dict[str, str] = {}
    for state, group in known.groupby(known["state"].astype(str)):
        regions = sorted(set(group["region"].astype(str)))
        if len(regions) > 1:
            raise ValueError(
                f"State {state!r} is labelled with multiple regions {regions}; the source "
                "vocabulary is inconsistent and imputation cannot proceed unambiguously."
            )
        observed[state] = regions[0]

    mapping: dict[str, str] = dict(observed)
    for state in df["state"].dropna().astype(str).unique():
        if state in mapping:
            continue
        candidate = CENSUS_REGION_BY_STATE.get(state)
        # WHY ``candidate in vocabulary`` and not just ``candidate``: this single
        # condition is the entire defence against inventing a region name.
        mapping[state] = candidate if candidate in vocabulary else UNKNOWN_REGION
    return mapping


def impute_region(df: pd.DataFrame, audit: AuditLog, source_null_count: int | None = None) -> pd.DataFrame:
    """Fill NULL regions from the column's own vocabulary and flag the fill.

    ── ST-03 · NULL region ─────────────────────────────────────────────────────
    WHY: S013 (Cascade Station) and S014 (Lloyd Center), both Portland OR, have
      no region. Left NULL, both stores disappear from every regional roll-up --
      not with an error, just with a smaller "West" than reality.
    DECISION: impute via :func:`build_state_region_map`, which draws the *label*
      exclusively from values already present in the column. OR resolves to
      "West" because AZ and WA -- the only other Pacific/Mountain states in the
      file -- are already labelled "West". ``region_is_imputed`` is set to True on
      exactly those rows so anyone needing source-only figures can exclude them.
    ALTERNATIVE REJECTED: hard-coding a state-to-region table from an external
      source. That is what produced ``NY -> "East"`` in the previous attempt,
      inventing a region the data does not use and silently corrupting AOV by
      region. Any imputed value must come from the observed vocabulary.
    ALTERNATIVE REJECTED: leaving the region NULL. Two real stores with real
      revenue then vanish from regional analysis, understating the West, and
      nothing in the output says so.
    ALTERNATIVE REJECTED: dropping the two stores. Never on the table -- they
      carry transactions, and removing them converts a labelling gap into a
      revenue error.

    Args:
        df: Frame with ``state`` and ``region``. Normally the de-duplicated one.
        audit: Ledger, mutated in place.
        source_null_count: Number of NULL regions counted on the *raw* frame,
            before de-duplication. WHY this parameter exists: ``DefectRecord``
            counts source rows observed, and if a contested duplicate had ever
            carried a NULL region, counting after survivorship would under-report
            it. Defaults to the count on ``df`` when not supplied.

    Returns:
        A copy with ``region`` filled and a boolean ``region_is_imputed`` column.

    Defects handled: ST-03.
    """
    out = df.copy()
    missing_mask = out["region"].isna()
    mapping = build_state_region_map(out)

    imputed_values = out.loc[missing_mask, "state"].astype(str).map(mapping)
    out.loc[missing_mask, "region"] = imputed_values  # DEFECT: ST-03
    out["region_is_imputed"] = missing_mask.fillna(False).to_numpy(dtype=bool)

    detected = int(missing_mask.sum()) if source_null_count is None else int(source_null_count)
    if detected:
        pairs = sorted(
            {
                f"{state} -> {mapping.get(state, UNKNOWN_REGION)}"
                for state in out.loc[missing_mask, "state"].astype(str)
            }
        )
        audit.record(
            DefectRecord(
                code=DefectCode.ST_03_NULL_REGION,
                detected_count=detected,
                action="imputed",
                affected_keys=out.loc[missing_mask, BUSINESS_KEY].astype(str).tolist(),
                notes=(
                    "Region imputed from state using ONLY labels already present in the "
                    f"column (observed vocabulary: "
                    f"{sorted(set(df['region'].dropna().astype(str)))}). Applied: "
                    + "; ".join(pairs)
                    + ". region_is_imputed=True on these rows. The previous solution "
                    "hard-coded NY -> 'East' while the data says 'Northeast', inventing a "
                    "region and corrupting AOV-by-region; the vocabulary check makes that "
                    "impossible here."
                ),
            )
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Typing and post-conditions
# ══════════════════════════════════════════════════════════════════════════════
def _finalize_types(df: pd.DataFrame) -> pd.DataFrame:
    """Cast to the warehouse-ready dtypes and fix column order.

    WHY typing happens here, at the end, rather than at read time: every defect
    in this file is only visible while the data is still text (contract §7b,
    ``read_csv_as_str``). Coercion is therefore the *last* step, applied
    deliberately once every check that needed the raw form has run.

    Defects handled: none directly (ST-01's padded ZIP survives because
        ``zip_code`` stays a string -- casting it to int here would undo the
        entire fix).
    """
    out = df.copy()
    for col in ("store_id", "store_name", "city", "state", "zip_code", "region"):
        out[col] = out[col].astype(str)
    out["opened_date"] = pd.to_datetime(out["opened_date"], format=OPENED_DATE_FORMAT)
    out["zip_is_suspect"] = out["zip_is_suspect"].astype(bool)
    out["region_is_imputed"] = out["region_is_imputed"].astype(bool)
    return out[list(OUTPUT_COLUMNS)].reset_index(drop=True)


def _assert_post_conditions(df: pd.DataFrame) -> None:
    """Fail loudly if the dimension is not loadable.

    WHY assertions rather than a logged warning: every condition below is a
    ``dim_store`` constraint. Discovering a duplicate ``store_id`` here produces
    a traceback pointing at the cleaning stage that caused it; discovering it in
    the loader produces an opaque SQLite IntegrityError two stages later, and
    discovering it *nowhere* means a metric is silently wrong.

    Raises:
        AssertionError: On a duplicate key, a null region, or a malformed ZIP
            width.

    Defects handled: ST-01, ST-02, ST-03 (verification).
    """
    duplicated = df[df["store_id"].duplicated(keep=False)]["store_id"].unique().tolist()
    assert not duplicated, f"ST-02 unresolved: duplicate store_id values remain: {duplicated}"
    assert df["store_id"].notna().all(), "store_id contains nulls; dim_store.store_id is NOT NULL"

    null_region = df.loc[df["region"].isna() | (df["region"].astype(str) == ""), "store_id"].tolist()
    assert not null_region, f"ST-03 unresolved: region still null for {null_region}"

    bad_width = df.loc[df["zip_code"].str.len() != ZIP_CODE_LENGTH, "store_id"].tolist()
    assert not bad_width, f"ST-01 unresolved: zip_code not {ZIP_CODE_LENGTH} chars for {bad_width}"

    assert df["opened_date"].notna().all(), "opened_date failed to parse for at least one store"


# ══════════════════════════════════════════════════════════════════════════════
# Orchestration
# ══════════════════════════════════════════════════════════════════════════════
def clean_stores(df: pd.DataFrame, audit: AuditLog) -> pd.DataFrame:
    """Clean the store dimension end to end.

    Stage order is a decision, not a formality:

    1. **Normalise text** -- so ``''`` and ``NaN`` are one kind of missing before
       anything counts nulls.
    2. **ST-01 ZIP** -- runs on all 16 *source* rows, so the detected count is a
       count of source rows (contract §7b) rather than of survivors.
    3. **ST-02 survivorship** -- runs *before* imputation, deliberately. The
       first survivorship criterion is "fewest nulls", which is only meaningful
       while the nulls are still there; imputing first would erase the very
       signal the rule ranks on.
    4. **ST-03 region imputation** -- runs on the de-duplicated frame, with the
       source-level null count passed in explicitly so the audit still reports
       source rows.
    5. **Type and assert** -- coerce last, then prove the result is loadable.

    Args:
        df: Raw, all-string stores frame from
            :func:`src.io_utils.read_csv_as_str`.
        audit: Ledger, mutated in place (contract §4).

    Returns:
        The 15-row store dimension with :data:`OUTPUT_COLUMNS`.

    Raises:
        KeyError: If a required source column is absent -- WHY raise: a missing
            column means the extract changed shape, and cleaning a frame we do
            not recognise would produce confident nonsense.
        AssertionError: If a post-condition fails (see
            :func:`_assert_post_conditions`).

    Defects handled: ST-01, ST-02, ST-03.
    """
    missing = [c for c in SOURCE_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"stores.csv is missing expected column(s): {missing}")

    # ── 1 · Normalise text ───────────────────────────────────────────────────
    working = normalize_text_columns(df, SOURCE_COLUMNS)

    # WHY capture this now: see impute_region's ``source_null_count`` argument.
    # Counting NULL regions on the 16-row source frame keeps the audit's
    # "detected" comparable with seed_data.py's "injected".
    source_null_regions = int(working["region"].isna().sum())

    # ── 2 · ST-01 ────────────────────────────────────────────────────────────
    working = normalize_zip_codes(working, audit)

    # ── 3 · ST-02 ────────────────────────────────────────────────────────────
    working = resolve_store_survivorship(working, audit)

    # ── 4 · ST-03 ────────────────────────────────────────────────────────────
    working = impute_region(working, audit, source_null_count=source_null_regions)

    # ── 5 · Type and prove ───────────────────────────────────────────────────
    cleaned = _finalize_types(working)
    _assert_post_conditions(cleaned)
    return cleaned


__all__ = [
    "BUSINESS_KEY",
    "CENSUS_REGION_BY_STATE",
    "OUTPUT_COLUMNS",
    "SOURCE_COLUMNS",
    "SURVIVORSHIP_CRITERIA",
    "UNKNOWN_REGION",
    "build_state_region_map",
    "clean_stores",
    "impute_region",
    "normalize_text_columns",
    "normalize_zip_codes",
    "resolve_store_survivorship",
]
