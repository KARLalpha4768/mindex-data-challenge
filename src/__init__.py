"""SUPERSEDED -- the repository-root ``src/`` package is a dead first attempt.

The submitted, maintained and tested implementation of this challenge lives in
``solution/src/``. This package is the *first* attempt at the same problem. It is kept
in the repository only as history, because owning a wrong turn honestly reads better
than a mysteriously absent directory -- and because several of the decisions the
submission argues for are only persuasive next to the version that got them wrong.

WHY IMPORTING THIS RAISES INSTEAD OF RE-EXPORTING ``solution.src``
-----------------------------------------------------------------
A transparent forward would make ``import src`` work from the repository root and
would therefore *hide* the duplication rather than surface it. The whole point of this
shim is that a reader who lands in the wrong tree finds out on the first line, not
three files later when a number disagrees with the README.

WHAT WAS WRONG WITH IT (all five are fixed in ``solution/src/``)
---------------------------------------------------------------
1. ``cleaner.py`` recomputed ``total_amount = unit_price * quantity``, destroying the
   TX-03 silent-discount finding and overstating net revenue by $961.48.
2. Date parsing used one ``pd.to_datetime(..., errors="coerce")`` call, so the 20
   TX-01 mixed-format rows were dropped -- and then misreported as "future dates",
   turning a parser bug into a fictitious business finding.
3. ``state_to_region`` mapped ``NY -> "East"`` while the data's own vocabulary is
   ``"Northeast"``, inventing a fifth region and corrupting AOV by region.
4. ``drop_duplicates(subset=["product_id"])`` swallowed PR-02, so P005's undocumented
   price change was reported as a harmless duplicate.
5. ``zip_code.astype(str).str.zfill(5)`` was applied unconditionally to every row.

WHERE TO GO INSTEAD
-------------------
::

    cd solution
    python -m src.pipeline --output-dir /tmp/run     # the maintained pipeline
    python -m pytest -q                              # the maintained suite

or, from the repository root, the single command that verifies the whole submission::

    python scripts/verify_submission.py

Nothing in the submission, the test suite or the dashboard imports this package.
"""

from __future__ import annotations

# WHY the raise sits at module scope rather than in a ``__getattr__`` hook: a lazy
# guard would let ``import src`` succeed and only fail on first attribute access,
# which is exactly the delayed, confusing failure this file exists to prevent.
# Raising here also neutralises every subpackage (``src.cleaning``, ``src.warehouse``,
# ``src.analytics``, ``src.profiling``) in one line, since Python must initialise the
# parent package before any of them.
raise RuntimeError(
    "The repository-root 'src/' package is a SUPERSEDED first attempt, retained only "
    "as history; it is not the submission and it contains known, documented bugs. "
    "The maintained implementation is 'solution/src/'. "
    "Run:  cd solution && python -m src.pipeline --output-dir /tmp/run   "
    "or, from the repository root:  python scripts/verify_submission.py"
)
