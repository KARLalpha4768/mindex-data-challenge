"""I/O boundary: string-faithful CSV reading, atomic JSON writing, tag scanning.

Three jobs, each of which exists to prevent a specific class of silent failure:

1. **Read every CSV as ``dtype=str``.** Type inference is the enemy of a
   data-quality audit -- see :func:`read_csv_as_str` for the full argument. In
   short: pandas' helpfulness destroys the evidence before anyone can look at it.
2. **Write JSON atomically, with an encoder that understands numpy and pandas.**
   A half-written ``analytics.json`` served to a dashboard is worse than no file
   at all, and ``TypeError: Object of type int64 is not JSON serializable`` on
   the last line of a five-minute run is a uniquely annoying way to lose work.
3. **Scan the source tree for ``# DEFECT: <CODE>`` tags** so the dashboard can
   link each defect card straight to the line that handles it.

Defect codes owned: none directly. But ``dtype=str`` is load-bearing for ST-01
(zip '0938' -> int 938), TX-01 (dates), TX-02 (currency strings) and PR-04
(0.00): every one of those defects is *only* visible because nothing was parsed
on the way in.

Inputs:  ``data/raw/*.csv`` and the ``src/`` tree.
Outputs: DataFrames of strings, JSON files, and a code index.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from src.config import RAW_DIR

# ── Raw dataset registry ──────────────────────────────────────────────────────
# WHY named here rather than globbed: a glob would silently pick up a stray
# file someone dropped into data/raw, and silently *not* fail if one of the
# three expected files went missing.
RAW_FILENAMES: dict[str, str] = {
    "stores": "stores.csv",
    "products": "products.csv",
    "transactions": "transactions.csv",
}

DEFECT_TAG_PATTERN: re.Pattern[str] = re.compile(r"#\s*DEFECT:\s*([A-Z]{2}-\d{2})")
"""Matches the contract §7.5 tag format ``# DEFECT: <CODE>``, one code per tag.

WHY the format is load-bearing and this regex is deliberately strict: the
dashboard builds its code links from these tags, so a tolerant regex that also
matched ``# DEFECTS: TX-03, TX-04`` would produce link targets that do not
correspond to the tagging convention the rest of the codebase follows.
"""


# ── CSV reading ───────────────────────────────────────────────────────────────
def read_csv_as_str(path: Path | str, *, keep_blank_as_na: bool = True) -> pd.DataFrame:
    """Read a CSV with **every column as a string**, exactly as it sits on disk.

    This is the single most important decision in the ingest layer, and it is
    the opposite of what pandas does by default.

    What type inference would destroy in *this* dataset:

    * **ST-01** -- ``zip_code`` ``'0938'`` is inferred as the integer ``938``.
      The leading zero, which is the entire story of the defect, is gone before
      any check can run; worse, the other 14 ZIPs also become integers, so
      ``'00938'`` and ``'14564'`` are no longer even the same kind of thing.
    * **TX-02** -- ``total_amount`` becomes an ``object`` column holding 480
      floats and 25 strings. Arithmetic on it either raises or, depending on
      the operation, silently concatenates.
    * **TX-01** -- date-like strings may be partially converted, with the
      day-first/month-first guess applied inconsistently.
    * **PR-04 / TX-07** -- ``0.00`` and ``0`` become indistinguishable from a
      missing value once they pass through a float column with NaNs in it.

    Reading everything as text means the pipeline parses each field
    *deliberately*, in a function that is named, tested and audited. Nothing is
    converted by accident, so nothing is corrupted by accident.

    Args:
        path: CSV file to read.
        keep_blank_as_na: When True (default) empty fields become ``NaN``, which
            is what every ``.isna()``-based null check downstream expects. Set
            False to receive literal empty strings instead -- occasionally
            useful when the distinction between "" and NULL matters.

    Returns:
        A DataFrame in which every column has dtype ``object`` holding ``str``
        (or ``NaN`` for blanks).

    Raises:
        FileNotFoundError: If the file does not exist. WHY not return an empty
            frame: a missing input must stop the run, not produce a clean report
            about zero rows.

    Defects handled: none directly -- but ST-01, TX-01, TX-02, PR-04 and TX-07
        are only *detectable* because of this function.
    """
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Expected raw input at {src} -- cannot continue.")
    return pd.read_csv(
        src,
        dtype=str,  # WHY: see docstring. Non-negotiable.
        keep_default_na=keep_blank_as_na,
        na_filter=keep_blank_as_na,
        # WHY skipinitialspace=False and no whitespace stripping here: trimming
        # is a cleaning decision that belongs in the cleaning layer where it can
        # be counted and audited, not a silent side effect of reading a file.
        skipinitialspace=False,
    )


def read_raw_datasets(raw_dir: Path | str = RAW_DIR) -> dict[str, pd.DataFrame]:
    """Read all three source files as strings.

    Args:
        raw_dir: Directory containing stores.csv, products.csv, transactions.csv.

    Returns:
        ``{"stores": df, "products": df, "transactions": df}``.

    Raises:
        FileNotFoundError: If any expected file is missing.

    Defects handled: none directly (ingest).
    """
    base = Path(raw_dir)
    return {name: read_csv_as_str(base / fname) for name, fname in RAW_FILENAMES.items()}


def write_dataframe_csv(df: pd.DataFrame, path: Path | str) -> Path:
    """Write a DataFrame to CSV, creating parent directories as needed.

    Args:
        df: Frame to write.
        path: Destination file.

    Returns:
        The path written.

    Defects handled: none (output helper).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # WHY index=False everywhere in this project: a pandas index written into a
    # CSV is an invitation to mistake a positional artefact for a business key.
    df.to_csv(target, index=False)
    return target


# ── JSON writing ──────────────────────────────────────────────────────────────
class PipelineJSONEncoder(json.JSONEncoder):
    """JSON encoder that speaks numpy, pandas, datetime, Decimal and Enum.

    WHY this is necessary rather than fussy: essentially every value that
    reaches ``json.dumps`` in this pipeline has been through pandas, which means
    an "int" is usually a ``numpy.int64`` and a "date" is usually a
    ``pandas.Timestamp``. The stock encoder rejects all of them with a
    ``TypeError`` raised at the very end of the run, after all the expensive
    work is done. Handling them once, here, means no stage has to remember to
    call ``.item()`` on its own aggregates.

    NaN handling deserves its own note: ``NaN``/``NaT``/``pd.NA`` all serialise
    to JSON ``null``. WHY: bare ``NaN`` is not valid JSON, and ``JSON.parse`` in
    the browser throws on it -- which would break the dashboard on exactly the
    rows that are most interesting, the ones with missing data.
    """

    def default(self, o: Any) -> Any:  # noqa: D102 - inherited contract
        # -- numpy scalars ----------------------------------------------------
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            value = float(o)
            return None if np.isnan(value) else value
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        # -- pandas -----------------------------------------------------------
        if isinstance(o, pd.Timestamp):
            # WHY isoformat and not str(): str() on a Timestamp yields
            # "2026-06-02 00:00:00", which JS Date parses inconsistently across
            # browsers. ISO-8601 does not have that problem.
            return o.isoformat()
        if o is pd.NaT or o is pd.NA:
            return None
        if isinstance(o, pd.Series):
            return o.tolist()
        if isinstance(o, pd.DataFrame):
            return dataframe_to_records(o)
        # -- stdlib -----------------------------------------------------------
        if isinstance(o, (dt.datetime, dt.date)):
            return o.isoformat()
        if isinstance(o, dt.timedelta):
            return o.total_seconds()
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, (set, frozenset)):
            # WHY sorted: sets have no order, so an unsorted dump would make
            # two identical runs produce byte-different JSON and pollute diffs.
            return sorted(o, key=str)
        if isinstance(o, Path):
            return str(o)
        if is_dataclass(o) and not isinstance(o, type):
            return asdict(o)
        return super().default(o)


def _scrub_nonfinite(obj: Any) -> Any:
    """Recursively replace NaN/Infinity floats with ``None`` before serialising.

    WHY this is needed even though :class:`PipelineJSONEncoder` handles numpy
    floats: ``numpy.float64`` is a *subclass* of Python ``float``, so the json
    module serialises it through the fast path and never calls ``default()``.
    With ``allow_nan=False`` that surfaces as a bare
    ``ValueError: Out of range float values are not JSON compliant`` -- no key
    name, no path, no clue which of ten thousand values was the problem.

    Scrubbing first means the documented contract actually holds: NaN, NaT and
    Infinity all become JSON ``null``, ``allow_nan=False`` stays on to catch
    anything that slips past, and the dashboard's ``JSON.parse`` never sees a
    token it cannot read. Infinity is included deliberately -- a rate metric
    that divided by zero must render as "no value", not as a number larger than
    every other bar on the chart.

    Args:
        obj: Any nested structure of dicts, lists, tuples and scalars.

    Returns:
        The same structure with every non-finite float replaced by ``None``.

    Defects handled: none (serialization helper).
    """
    if isinstance(obj, (bool, str, bytes)) or obj is None:
        # WHY this branch first: bool is a subclass of int and str is iterable;
        # testing them up front keeps the cheap cases cheap and unambiguous.
        return obj
    if isinstance(obj, float):  # also catches numpy.float64 via subclassing
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, np.floating):
        value = float(obj)
        return None if not math.isfinite(value) else value
    if isinstance(obj, dict):
        return {k: _scrub_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_nonfinite(v) for v in obj]
    return obj


def json_safe(obj: Any) -> Any:
    """Round-trip ``obj`` through the encoder to get plain-Python structures.

    Useful when a payload must be embedded inside a larger dict that some other
    library will serialise.

    Args:
        obj: Any structure the encoder above can handle.

    Returns:
        The equivalent structure built only from dict/list/str/int/float/bool/None.

    Defects handled: none (serialization helper).
    """
    encoded = json.dumps(_scrub_nonfinite(obj), cls=PipelineJSONEncoder, allow_nan=False)
    return json.loads(encoded)


def write_json_atomic(path: Path | str, payload: Any, *, indent: int = 2) -> Path:
    """Serialise ``payload`` to ``path`` atomically.

    The write goes to a temporary file in the *same directory* and is then moved
    into place with :func:`os.replace`.

    WHY atomic: ``output/analytics.json`` and ``output/dashboard_bundle.json``
    are read by a separate process (the dashboard). A plain ``open(path, "w")``
    truncates the destination immediately, so any reader arriving during the
    write -- or any crash mid-write -- sees a truncated file that parses as
    invalid JSON. ``os.replace`` is atomic on POSIX and on Windows, so a reader
    sees either the complete old file or the complete new one, never a fragment.

    WHY the same directory: ``os.replace`` is only atomic within a filesystem;
    a temp file in /tmp could land on a different device and degrade to a
    non-atomic copy.

    Args:
        path: Destination file.
        payload: Any JSON-serialisable structure (see :class:`PipelineJSONEncoder`).
        indent: Pretty-print indent. Kept at 2 because these files are meant to
            be read by humans in a diff, not just by the dashboard.

    Returns:
        The path written.

    Raises:
        TypeError: If the payload contains a type the encoder cannot handle --
            deliberately not swallowed, because a silently dropped field in an
            audit report is exactly the kind of thing this project argues against.

    Defects handled: none (output helper).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                # WHY scrub before dumping: NaN/Inf would otherwise abort the
                # write with an unlocatable ValueError. See _scrub_nonfinite.
                _scrub_nonfinite(payload),
                handle,
                cls=PipelineJSONEncoder,
                indent=indent,
                ensure_ascii=False,
                # WHY allow_nan=False: Python happily emits bare NaN/Infinity,
                # which is not legal JSON and crashes JSON.parse in the browser.
                # Forcing an error here means the encoder above must convert
                # them to null, which it does.
                allow_nan=False,
                sort_keys=False,
            )
            handle.flush()
            os.fsync(handle.fileno())  # WHY: survive a crash between write and replace.
        os.replace(tmp_path, target)
    except BaseException:
        # WHY BaseException: a KeyboardInterrupt mid-write should still clean up
        # the temp file rather than leaving .analytics.json.xxxx.tmp litter.
        tmp_path.unlink(missing_ok=True)
        raise
    return target


def read_json(path: Path | str) -> Any:
    """Read a JSON file written by this pipeline.

    Args:
        path: File to read.

    Returns:
        The parsed structure.

    Defects handled: none (I/O helper).
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    """Convert a DataFrame to JSON-safe records.

    Args:
        df: Frame to convert.
        limit: Optional row cap, applied from the top.

    Returns:
        A list of dicts with NaN/NaT replaced by ``None`` and numpy scalars
        downcast to Python types.

    Defects handled: none (serialization helper).
    """
    frame = df if limit is None else df.head(limit)
    # WHY the object cast plus where(notna): DataFrame.to_dict leaves float NaN
    # in place, and NaN is not valid JSON. Doing it here means every caller gets
    # browser-safe output without thinking about it.
    cleaned = frame.astype(object).where(pd.notna(frame), None)

    def scalar(value: Any) -> Any:
        # WHY convert here rather than relying on PipelineJSONEncoder: this
        # function promises *JSON-safe* records, and callers legitimately pass
        # the result to a plain json.dumps or embed it in another payload. A
        # Timestamp surviving this call would blow up somewhere far away, with
        # a traceback that points at the wrong module.
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, (dt.datetime, dt.date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, float) and np.isnan(value):
            return None
        return value

    return [
        {str(k): scalar(v) for k, v in row.items()}
        for row in cleaned.to_dict(orient="records")
    ]


# ── Source-tree scanning ──────────────────────────────────────────────────────
def scan_defect_tags(
    root: Path | str,
    *,
    patterns: Iterable[str] = ("*.py", "*.sql"),
    exclude_names: Iterable[str] = ("defects.py",),
) -> dict[str, list[dict[str, Any]]]:
    """Build a code index from ``# DEFECT: <CODE>`` tags in the source tree.

    The dashboard turns this into "show me the line that handles TX-03" links,
    which is the difference between a reviewer taking a claim on trust and
    verifying it in two clicks.

    Args:
        root: Directory to walk (normally ``src/``).
        patterns: Glob patterns to include. SQL is included because
            ``warehouse/schema.sql`` and the analytics queries also carry tags.
        exclude_names: Filenames to skip. ``defects.py`` is excluded by default
            because it *declares* all 17 codes; indexing it would make the
            catalog appear to be the handler for every defect and drown the
            genuine handler locations in noise.

    Returns:
        ``{"TX-03": [{"file": "src/cleaning/transactions.py", "line": 118,
        "snippet": "..."}], ...}``. Paths are POSIX-style and relative to
        ``root.parent`` -- WHY relative: absolute Windows paths embedded in a
        JSON file that a web app renders would leak the developer's home
        directory and would not resolve for anyone else.

    Defects handled: all 17 (indexing only -- it reports where they are handled,
        it does not handle them).

    Note:
        A code with an empty list here means the defect is in the catalog and
        possibly even implemented, but the handling line was never tagged. That
        is a documentation gap worth surfacing, so the pipeline reports it
        rather than hiding it behind a default.
    """
    base = Path(root)
    anchor = base.parent
    skip = set(exclude_names)
    index: dict[str, list[dict[str, Any]]] = {}

    files: list[Path] = []
    for pattern in patterns:
        files.extend(base.rglob(pattern))

    for file in sorted(set(files)):
        if file.name in skip or "__pycache__" in file.parts:
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = DEFECT_TAG_PATTERN.search(line)
            if not match:
                continue
            code = match.group(1)
            try:
                rel = file.relative_to(anchor).as_posix()
            except ValueError:  # pragma: no cover - defensive
                rel = file.name
            index.setdefault(code, []).append(
                {
                    "file": rel,
                    "line": lineno,
                    # WHY cap the snippet: a tagged line inside a long expression
                    # could be hundreds of characters and would blow up the
                    # bundle size for no readability gain.
                    "snippet": line.strip()[:200],
                }
            )
    return index


def ensure_output_dirs(*directories: Path | str) -> None:
    """Create every directory in ``directories`` (and parents) if absent.

    Args:
        *directories: Paths to create.

    Defects handled: none (housekeeping).
    """
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


__all__ = [
    "DEFECT_TAG_PATTERN",
    "PipelineJSONEncoder",
    "RAW_FILENAMES",
    "dataframe_to_records",
    "ensure_output_dirs",
    "json_safe",
    "read_csv_as_str",
    "read_json",
    "read_raw_datasets",
    "scan_defect_tags",
    "write_dataframe_csv",
    "write_json_atomic",
]
