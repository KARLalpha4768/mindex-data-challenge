"""SUPERSEDED -- the first attempt's analytics runner. Do not read this file as the submission.

This module belongs to the repository-root ``src/`` package, which is the **first
attempt** at the Mindex data-engineering challenge. It is retained only as history.
The maintained implementation is::

    solution/src/analytics/ (queries.py, runner.py)

The maintained metrics are named SQL constants executed against the warehouse, each
serialised with its own `sql`, `sql_ref`, `definition_note` and `column_units`.

Importing this module raises immediately. It is deliberately **not** a transparent
re-export of the maintained code: forwarding would make the wrong import path work and
would hide the duplicated tree instead of surfacing it. See ``src/__init__.py`` for the
full list of what this attempt got wrong, and the root ``README.md`` for why the
directory is still here.

Defects handled: none. This file is inert.
"""

from __future__ import annotations

raise RuntimeError(
    "src/analytics.py at the repository root is a SUPERSEDED first attempt and is retained "
    "only as history -- it is not the submission. The maintained implementation is "
    "solution/src/analytics/ (queries.py, runner.py). "
    "Run:  cd solution && python -m src.pipeline --output-dir /tmp/run   "
    "or, from the repository root:  python scripts/verify_submission.py"
)
