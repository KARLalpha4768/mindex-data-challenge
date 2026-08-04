"""The decision ledger: what the pipeline found, what it did, and whether that
matches what is provably in the source data.

Cleaning code that does not write down what it did is unreviewable. Every
cleaning function in this project takes an :class:`AuditLog`, mutates it, and
returns only the DataFrame (contract §4) -- so the ledger accumulates a complete,
ordered account of the run without any stage needing to know about any other.

The headline feature is :meth:`AuditLog.assert_all_expected_defects_found`. It
joins the counts the pipeline *detected* against the counts
``scripts/seed_data.py`` provably *injected* (via :data:`src.defects.DEFECT_CATALOG`)
and returns a list of human-readable mismatches. An empty list is the pipeline's
proof of completeness; a non-empty list fails the run. This inverts the usual
data-quality posture: instead of hoping the checks caught everything, the run
asserts it and dies if it did not.

Defect codes owned: none directly -- this module records the work that
``src/cleaning/*`` performs against all 17.

Inputs:  :class:`DefectRecord` instances and quarantined DataFrames.
Outputs: ``output/audit_report.json``, ``output/quarantine/<dataset>__<code>.csv``,
         and a markdown summary table for the README and the console.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from src.config import AS_OF_DATE, MAX_AFFECTED_KEYS_SERIALIZED, QUARANTINE_DIR
from src.defects import CATALOG_VERSION, DEFECT_CATALOG, DefectCode, DefectSpec

# ── Controlled vocabulary for actions ─────────────────────────────────────────
# WHY a closed set: "action" is rendered as a badge in the dashboard and grouped
# on in the README. If one cleaner writes "dropped" and another writes "removed"
# the grouping silently splits and the summary understates what happened.
VALID_ACTIONS: frozenset[str] = frozenset(
    {"dropped", "imputed", "flagged", "quarantined", "preserved"}
)


@dataclass
class DefectRecord:
    """One cleaning stage's report about one defect class.

    Attributes:
        code: Which defect this record is about.
        detected_count: Number of *source rows* affected. This is compared
            against ``DefectSpec.expected_count``, so it must count rows in the
            raw input, not rows surviving some later filter -- otherwise the
            completeness proof compares two different things.
        action: One of :data:`VALID_ACTIONS`. What the pipeline did about it.
        affected_keys: Business keys (store_id / product_id / transaction_id),
            truncated only at serialization time so in-memory assertions in the
            test suite still see the full set.
        notes: Free text for anything a reviewer would want to know -- the
            dollar value withheld, the survivorship winner, the assumption made.

    Defects handled: all 17 (this is the carrier type).
    """

    code: DefectCode
    detected_count: int
    action: str
    affected_keys: list[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        # WHY normalise here rather than trusting callers: six different modules
        # written by different hands construct these. Normalising at the door
        # means the ledger is consistent no matter who wrote the call site.
        if not isinstance(self.code, DefectCode):
            self.code = DefectCode(str(self.code))
        self.action = str(self.action).strip().lower()
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"{self.code}: action {self.action!r} is not one of {sorted(VALID_ACTIONS)}"
            )
        if self.detected_count < 0:
            raise ValueError(f"{self.code}: detected_count must be >= 0")
        # WHY str() every key: keys arrive from pandas as numpy scalars often
        # enough that json.dumps would choke on them much later, in a place with
        # no useful traceback.
        self.affected_keys = [str(k) for k in self.affected_keys]

    @property
    def spec(self) -> DefectSpec:
        """The catalog entry this record is evidence for."""
        return DEFECT_CATALOG[self.code]


class AuditLog:
    """Accumulating ledger of detections, decisions and quarantined rows.

    Construct with no arguments; every cleaning function receives the same
    instance and mutates it in place::

        audit = AuditLog()
        stores = clean_stores(raw_stores, audit)
        products = clean_products(raw_products, audit)

    Merge semantics (deliberate, and the reason this is a class rather than a
    list): calling :meth:`record` twice for the same code **merges** the two
    records -- counts are summed, keys unioned in first-seen order, notes
    concatenated. It does not overwrite and it does not raise.

    WHY sum rather than raise: a defect can legitimately be detected in more
    than one pass. TX-04 and TX-05 are both found by the same referential
    integrity function, and a future cleaner might report zero-quantity rows
    from two code paths. Raising would force artificial bookkeeping in the
    cleaners just to keep the ledger happy. Overwriting was rejected outright:
    it is the silent-failure mode this whole project exists to demonstrate
    against -- the second call would quietly discard the first call's evidence
    and the completeness check would then under-report with no trace. Every
    merge increments ``merge_count`` and is visible in the JSON output, so a
    reviewer can always see that a number is a sum and go looking for why.

    Attributes:
        as_of_date: Reference date stamped into the report for reproducibility.
        started_at: Wall-clock run start -- provenance metadata only, never
            used in any calculation.
    """

    def __init__(self, as_of_date: dt.date = AS_OF_DATE) -> None:
        self.as_of_date: dt.date = as_of_date
        self.started_at: dt.datetime = dt.datetime.now()
        # WHY a dict keyed by code rather than an append-only list: the ledger's
        # job is to answer "how many of X did we find", once, per code. Ordering
        # is preserved anyway because dicts are insertion-ordered in 3.7+.
        self._records: dict[DefectCode, DefectRecord] = {}
        self._merge_counts: dict[DefectCode, int] = {}
        self._quarantine: dict[tuple[str, DefectCode], pd.DataFrame] = {}
        self._quarantine_paths: dict[str, str] = {}
        self._events: list[str] = []

    # ── Recording ────────────────────────────────────────────────────────────
    def record(self, rec: DefectRecord) -> None:
        """Add a detection to the ledger, merging with any existing entry.

        Args:
            rec: The record to add.

        Returns:
            None. The ledger is mutated in place (contract §4: audit is mutated,
            never returned separately).

        Defects handled: all 17 (dispatch point).
        """
        existing = self._records.get(rec.code)
        if existing is None:
            self._records[rec.code] = rec
            self._events.append(f"record {rec.code} n={rec.detected_count} action={rec.action}")
            return

        # ── Merge path ───────────────────────────────────────────────────────
        # WHY dict.fromkeys instead of set(): preserves first-seen ordering, so
        # affected_keys stays deterministic across runs and diffs of
        # audit_report.json stay readable.
        merged_keys = list(dict.fromkeys([*existing.affected_keys, *rec.affected_keys]))
        merged_action = (
            existing.action
            if existing.action == rec.action
            # WHY "a+b" and not a list: action is a display badge; a compound
            # string keeps the JSON shape stable while making the split visible.
            else f"{existing.action}+{rec.action}"
        )
        merged_notes = " | ".join(n for n in (existing.notes, rec.notes) if n)
        self._records[rec.code] = DefectRecord(
            code=rec.code,
            detected_count=existing.detected_count + rec.detected_count,
            action=merged_action if merged_action in VALID_ACTIONS else "flagged",
            affected_keys=merged_keys,
            notes=merged_notes,
        )
        # WHY re-assign action after construction: DefectRecord validates the
        # action against the closed vocabulary, and a compound "dropped+flagged"
        # is intentionally outside it. We construct with a legal placeholder,
        # then set the compound value so the report shows what really happened.
        self._records[rec.code].action = merged_action
        self._merge_counts[rec.code] = self._merge_counts.get(rec.code, 1) + 1
        self._events.append(
            f"merge {rec.code} +{rec.detected_count} -> {self._records[rec.code].detected_count}"
        )

    def quarantine(self, dataset: str, df: pd.DataFrame, code: DefectCode) -> None:
        """Set aside rows the pipeline refuses to load, so the loss is auditable.

        Args:
            dataset: Source dataset name -- becomes the filename prefix.
            df: The rejected rows. Copied defensively, because the caller
                almost always continues to mutate the frame it sliced this from.
            code: The defect that caused the rejection.

        Returns:
            None.

        Defects handled: TX-04, TX-05, TX-07, TX-08 and PR-02/PR-04 evidence
            rows -- anything excluded from the warehouse or needing human review.
        """
        if df is None:  # pragma: no cover - defensive
            return
        key = (dataset, code if isinstance(code, DefectCode) else DefectCode(str(code)))
        frame = df.copy()
        if key in self._quarantine:
            # WHY concat rather than replace: a second call for the same
            # (dataset, code) is additional evidence, not a correction. Silently
            # replacing would discard rows that a reviewer was told exist.
            frame = pd.concat([self._quarantine[key], frame], ignore_index=True)
        self._quarantine[key] = frame
        self._events.append(f"quarantine {dataset}/{key[1]} rows={len(frame)}")

    # ── Read access ──────────────────────────────────────────────────────────
    @property
    def records(self) -> list[DefectRecord]:
        """All records, in first-recorded order."""
        return list(self._records.values())

    def get(self, code: DefectCode) -> DefectRecord | None:
        """Return the record for ``code``, or ``None`` if never recorded."""
        return self._records.get(code)

    def detected_count(self, code: DefectCode) -> int:
        """Detected count for ``code``, or 0 if it was never recorded."""
        rec = self._records.get(code)
        return rec.detected_count if rec else 0

    @property
    def quarantined_row_count(self) -> int:
        """Total rows currently held in quarantine across all datasets/codes."""
        return int(sum(len(df) for df in self._quarantine.values()))

    # ── The completeness proof ───────────────────────────────────────────────
    def assert_all_expected_defects_found(self) -> list[str]:
        """Compare detected counts against the catalog's expected counts.

        This is the reviewer-facing proof that the pipeline found everything
        that is actually in the data, rather than everything it happened to look
        for. Two independent failure modes are reported:

        1. **Never recorded** -- a catalog code that no cleaning stage touched
           at all. This is the dangerous one: a check that was never wired up
           produces no error, no row change and no evidence, so without this
           test it is indistinguishable from a check that found nothing.
        2. **Count mismatch** -- the code was checked but the number disagrees
           with ``scripts/seed_data.py``. Under-counting means the detector is
           too narrow; over-counting usually means it is catching legitimate
           rows and something is about to be wrongly cleaned.

        Specs with ``expected_count is None`` are exempt from (2) but still
        subject to (1).

        Returns:
            A list of human-readable mismatch strings, empty when the run is
            provably complete. WHY return rather than ``assert``: the pipeline
            wants to print all of them, write them into audit_report.json, and
            *then* exit non-zero. An assert would surface exactly one and lose
            the report.

        Defects handled: all 17 (verification).
        """
        mismatches: list[str] = []

        for code, spec in DEFECT_CATALOG.items():
            rec = self._records.get(code)

            # ── Failure mode 1: the check never ran ──────────────────────────
            if rec is None:
                expected = "an unknown number of" if spec.expected_count is None else (
                    f"{spec.expected_count}"
                )
                mismatches.append(
                    f"NEVER RECORDED: {code.value} ({spec.dataset}) '{spec.title}' -- "
                    f"seed_data.py injects {expected} occurrence(s) but no pipeline stage "
                    f"recorded this defect class at all. Expected handler: {spec.source_ref}."
                )
                continue

            # ── Failure mode 2: the count disagrees with the source of truth ──
            if spec.expected_count is None:
                continue
            if rec.detected_count != spec.expected_count:
                delta = rec.detected_count - spec.expected_count
                direction = "over-counted by" if delta > 0 else "short by"
                mismatches.append(
                    f"COUNT MISMATCH: {code.value} ({spec.dataset}) '{spec.title}' -- "
                    f"expected {spec.expected_count}, detected {rec.detected_count} "
                    f"({direction} {abs(delta)}). Detection rule: {spec.detection}"
                )

        return mismatches

    def is_complete(self) -> bool:
        """True when :meth:`assert_all_expected_defects_found` finds nothing."""
        return not self.assert_all_expected_defects_found()

    # ── Presentation ─────────────────────────────────────────────────────────
    def summary_table(self) -> list[dict[str, str]]:
        """Rows for a markdown/HTML defect-coverage table, in catalog order.

        Every catalog code appears, including ones that were never recorded --
        WHY: a coverage table that only lists what was found cannot show what
        was missed, which is the single most useful thing it could show.

        Returns:
            A list of string-valued dicts with stable column keys:
            ``Code, Dataset, Severity, Title, Expected, Detected, Status, Action``.

        Defects handled: all 17 (reporting).
        """
        rows: list[dict[str, str]] = []
        for code, spec in DEFECT_CATALOG.items():
            rec = self._records.get(code)
            expected = "-" if spec.expected_count is None else str(spec.expected_count)
            if rec is None:
                status, detected, action = "NOT DETECTED", "0", "-"
            elif spec.expected_count is None or rec.detected_count == spec.expected_count:
                status, detected, action = "OK", str(rec.detected_count), rec.action
            else:
                status, detected, action = "MISMATCH", str(rec.detected_count), rec.action
            rows.append(
                {
                    "Code": code.value,
                    "Dataset": spec.dataset,
                    "Severity": str(spec.severity),
                    "Title": spec.title,
                    "Expected": expected,
                    "Detected": detected,
                    "Status": status,
                    "Action": action,
                }
            )
        return rows

    def summary_markdown(self, columns: Iterable[str] | None = None) -> str:
        """Render :meth:`summary_table` as a GitHub-flavoured markdown table.

        Args:
            columns: Optional column subset/ordering. Defaults to all columns.

        Returns:
            A markdown string, ready to paste into the README or print to a
            terminal.

        Defects handled: all 17 (reporting).
        """
        rows = self.summary_table()
        if not rows:  # pragma: no cover - impossible while the catalog is non-empty
            return "_no defect records_"
        cols = list(columns) if columns else list(rows[0].keys())
        # WHY escape pipes: defect titles are free prose written by humans; one
        # stray '|' would silently shear the table in the rendered README.
        def cell(value: str) -> str:
            return str(value).replace("|", "\\|")

        header = "| " + " | ".join(cols) + " |"
        divider = "| " + " | ".join("---" for _ in cols) + " |"
        body = ["| " + " | ".join(cell(r.get(c, "")) for c in cols) + " |" for r in rows]
        return "\n".join([header, divider, *body])

    # ── Serialization ────────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        """Build the ``output/audit_report.json`` payload.

        Each record is joined to its :class:`~src.defects.DefectSpec`, so the
        report is **self-describing**: a reader needs no other file to
        understand what a code means, how it was detected, what was decided and
        why. That matters because this JSON is what the dashboard renders and
        what a reviewer is most likely to open first.

        Returns:
            A JSON-ready dict. Use :func:`src.io_utils.write_json_atomic` to
            persist it -- the encoder there handles the numpy/pandas scalar
            types that inevitably leak in through ``affected_keys``.

        Defects handled: all 17 (reporting).
        """
        mismatches = self.assert_all_expected_defects_found()
        recorded = set(self._records)
        never_recorded = [c.value for c in DEFECT_CATALOG if c not in recorded]

        records: list[dict[str, Any]] = []
        for code, spec in DEFECT_CATALOG.items():
            rec = self._records.get(code)
            keys = rec.affected_keys if rec else []
            records.append(
                {
                    # -- what happened -------------------------------------------------
                    "code": code.value,
                    "detected": rec is not None,
                    "detected_count": rec.detected_count if rec else 0,
                    "expected_count": spec.expected_count,
                    "count_matches": (
                        rec is not None
                        and (
                            spec.expected_count is None
                            or rec.detected_count == spec.expected_count
                        )
                    ),
                    "action": rec.action if rec else None,
                    "notes": rec.notes if rec else "",
                    "merged_from_calls": self._merge_counts.get(code, 1 if rec else 0),
                    # -- who it happened to --------------------------------------------
                    "affected_keys": keys[:MAX_AFFECTED_KEYS_SERIALIZED],
                    "affected_keys_total": len(keys),
                    "affected_keys_truncated": len(keys) > MAX_AFFECTED_KEYS_SERIALIZED,
                    # -- the joined spec, so the file explains itself -------------------
                    "dataset": spec.dataset,
                    "title": spec.title,
                    "severity": str(spec.severity),
                    "detection": spec.detection,
                    "decision": spec.decision,
                    "rationale": spec.rationale,
                    "source_ref": spec.source_ref,
                }
            )

        return {
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "as_of_date": self.as_of_date.isoformat(),
            "catalog_version": CATALOG_VERSION,
            "totals": {
                "defect_classes_in_catalog": len(DEFECT_CATALOG),
                "defect_classes_detected": len(self._records),
                "defect_classes_never_recorded": len(never_recorded),
                "rows_affected_total": sum(r.detected_count for r in self._records.values()),
                "rows_quarantined": self.quarantined_row_count,
                "mismatch_count": len(mismatches),
            },
            # WHY the top-level pass/fail is a named boolean rather than an
            # inference from mismatches == []: the dashboard and CI both branch
            # on it, and neither should have to re-derive the rule.
            "complete": not mismatches,
            "mismatches": mismatches,
            "never_recorded": never_recorded,
            "records": records,
            "quarantine_files": dict(self._quarantine_paths),
            "summary_table": self.summary_table(),
            "event_log": self._events,
        }

    def write_quarantine_files(self, directory: Path | None = None) -> dict[str, str]:
        """Write every quarantined slice to ``<dataset>__<code>.csv``.

        Args:
            directory: Target directory. Defaults to
                :data:`src.config.QUARANTINE_DIR`.

        Returns:
            ``{"transactions__TX-04": "/abs/path/transactions__TX-04.csv", ...}``,
            also stored on the log so :meth:`to_dict` can reference the files.

        Defects handled: TX-04, TX-05, TX-07, TX-08, PR-02, PR-04 (whichever
            were quarantined during the run).
        """
        target = Path(directory) if directory is not None else QUARANTINE_DIR
        target.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for (dataset, code), df in self._quarantine.items():
            name = f"{dataset}__{code.value}"
            path = target / f"{name}.csv"
            # WHY index=False: the pandas index here is a meaningless remnant of
            # whichever slice produced the frame; writing it invites someone to
            # mistake it for a source row number.
            df.to_csv(path, index=False)
            written[name] = str(path)
        self._quarantine_paths = written
        return written


__all__ = ["AuditLog", "DefectRecord", "VALID_ACTIONS"]
